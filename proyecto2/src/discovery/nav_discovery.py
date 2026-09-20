# proyecto2/src/discovery/nav_discovery.py
# -*- coding: utf-8 -*-
"""
Descubrimiento y descarga de series NAV historicas via Morningstar (mstarpy 8.x).

Instalacion previa:
    pip install mstarpy

Modos de uso:

  --mode discover   Verifica que el ISIN existe en Morningstar y registra
                    el ms_id interno + rango de fechas en nav_sources.
                    No descarga NAV. (~2s por fondo)

  --mode load       Descarga series historicas completas para ISINs con
                    status=OK en nav_sources y las carga en fund_nav_monthly.
                    Puede tardar horas para el universo completo.

  --mode update     Descarga solo los NAV desde el ultimo mes conocido.
                    Para ejecucion mensual automatizada.

Ejemplos:
    cd c:/desarrollo/fondos

    # Validar con 1 ISIN (sin escribir nada)
    python -m proyecto2.src.discovery.nav_discovery --mode discover --isin LU1873127366 --dry-run

    # Validar con 5 ISINs aleatorios
    python -m proyecto2.src.discovery.nav_discovery --mode discover --sample 5 --dry-run

    # Descubrimiento completo (~30-90 min)
    python -m proyecto2.src.discovery.nav_discovery --mode discover

    # Descarga historica desde 2000 (una sola vez)
    python -m proyecto2.src.discovery.nav_discovery --mode load --desde 2000-01-01

    # Actualizacion mensual
    python -m proyecto2.src.discovery.nav_discovery --mode update
"""

import argparse
import re
import requests
import sqlite3
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ============================================================
# Constantes API Morningstar (acceso directo, sin mstarpy constructor)
# ============================================================
# Screener: resuelve ISIN -> securityID (code interno)
_MS_SCREENER_URL  = "https://global.morningstar.com/api/v1/{lang}/tools/screener/_data"
# Performance mensual (mantenido como fallback; ya no es el camino principal de descarga)
_MS_PERF_URL      = "https://api-global.morningstar.com/sal-service/v1/fund/performance/v4/{code}"
_MS_APIKEY        = "lstzFDEOhfFNMLikKa0am9mgEKLBl49T"
_MS_PERF_PARAMS   = {"clientId": "MDC", "version": "4.71.0"}
# Chartservice: serie diaria de totalReturn (bearer auth via mstarpy.security.token_chart)
_MS_CHART_URL     = "https://www.us-api.morningstar.com/QS-markets/chartservice/v2/timeseries"

# Resolver: lt.morningstar.com security_details (componente de datos web)
# Verificado operativo en julio 2026 cuando SecuritySearch.ashx y el
# endpoint screener/_data devuelven 202 (bot-challenge de Akamai).
_LT_RESOLVE_URL    = "https://lt.morningstar.com/api/rest.svc/klr5zyak8x/security_details/{isin}"
_LT_RESOLVE_PARAMS = {"idtype": "isin", "viewId": "snapshot", "currencyId": "EUR"}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

def _random_ua() -> str:
    return random.choice(_USER_AGENTS)

# Path setup
_P2_SRC = Path(__file__).resolve().parent.parent
_ROOT   = _P2_SRC.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_P2_SRC.parent))

from shared.db import get_connection, is_postgres_connection, executemany

try:
    import mstarpy
    from mstarpy.security import token_chart as _ms_token_chart
    from mstarpy.security import random_user_agent as _ms_random_ua
except ImportError:
    print("\n[ERROR] mstarpy no esta instalado. Ejecuta: pip install mstarpy\n")
    sys.exit(1)

# ============================================================
# Configuracion
# ============================================================

MS_LANGUAGE       = "en-gb"       # idioma para la API Morningstar
MS_DELAY_DISCOVER = (0.5, 1.0)   # pausa en discover (solo existence check)
MS_DELAY_LOAD_OK  = (0.3, 0.8)   # chartservice REST tolera cadencia rapida (era 1.5-3.0)
MS_DELAY_LOAD_ERR = (0.1, 0.3)   # pausa tras 401/fallo (no es rate limit, ir rapido)
MS_DELAY_RESOLVE  = (0.5, 1.5)   # pausa tras instanciar Funds (llamada al screener)
NAV_FREQUENCY     = "daily"      # mstarpy 8 solo garantiza daily; resampleamos a mensual

# Backoff exponencial para errores de red transitorios (429, timeout, DNS)
MS_RETRY_MAX      = 3            # intentos maximos por fondo (sal-service / discover)
MS_BACKOFF_BASE   = 30           # segundos base (30 -> 90 -> 270)
MS_BACKOFF_FACTOR = 3            # multiplicador entre intentos

# Backoff para chartservice (API REST rapida — errores transitorios se recuperan pronto)
_CHART_RETRY_MAX     = 2         # max 2 intentos (falla rapido en bulk)
_CHART_BACKOFF_429   = 30        # 429 rate-limit: esperar 30s antes de reintentar
_CHART_BACKOFF_OTHER = 5         # 5xx: esperar 5s (error puntual del servidor)

# Pausa larga periodica - solo activa si hay exitos frecuentes
MS_COOLDOWN_EVERY = 200          # cada N fondos OK (no total)
MS_COOLDOWN_SECS  = (30, 60)     # reducido - 401 no necesita cooldown

# Numero de dias sin nuevo NAV (con last_checked reciente) tras el cual un fondo
# se clasifica como 'STALE_FROZEN' — fuente estructuralmente detenida (p.ej. fondo
# suspendido, sancionado o dado de baja por el proveedor). Por encima de este umbral
# el fondo se excluye del bucle de actualizacion (no se realizan llamadas de red).
# Elegido muy por encima del umbral de aviso [NAV STALE] (60 d) para no confundir
# retrasos operacionales con suspension permanente.
_FROZEN_NAV_DAYS  = 365


# ============================================================
# Resolucion de ISIN -> securityID interno de Morningstar
# ============================================================

# NOTA HISTORICA: el endpoint SecuritySearch.ashx (y el screener/_data
# de global.morningstar.com) devuelven 202 vacio (bot-challenge Akamai)
# desde ~julio 2026. El nuevo resolver usa lt.morningstar.com, que no
# esta afectado y devuelve XML con <Security id="..."> para cualquier
# ISIN valido en Morningstar.

def _resolve_isin(isin: str) -> Optional[dict]:
    """
    Resuelve un ISIN al securityID (code) interno de Morningstar usando
    el endpoint lt.morningstar.com/security_details (componente de datos web).

    Devuelve uno de tres valores:
        dict {"code": str, "name": str}   -- ISIN encontrado
        None                               -- ISIN genuinamente no existe
        dict {"challenge": True}           -- Endpoint bloqueado o error de red
                                             transitorio. El llamante NO debe
                                             escribir NOT_FOUND en la BD.

    El "code" devuelto es el securityID (ej. F0GBR04K6R) compatible con
    el endpoint sal-service/.../performance/v4/{code} de descarga NAV.
    """
    url = _LT_RESOLVE_URL.format(isin=isin)
    try:
        r = requests.get(
            url,
            params=_LT_RESOLVE_PARAMS,
            headers={"user-agent": _random_ua()},
            timeout=15,
        )
    except requests.RequestException:
        return {"challenge": True}

    if r.status_code != 200:
        # 202 = bot-challenge; 5xx = error transitorio; cualquier no-200
        return {"challenge": True}

    text = r.content.decode("utf-8", errors="replace")
    m_code = re.search(r'<Security\s+id="([^"]+)"', text)
    if not m_code:
        # 200 pero sin <Security id="..."> => genuinamente no existe
        return None

    code = m_code.group(1)
    m_name = re.search(r"<Name>([^<]+)</Name>", text)
    name = m_name.group(1) if m_name else ""
    return {"code": code, "name": name}


# ============================================================
# Descarga de rango de fechas
# ============================================================

def _get_nav_range(fund) -> Optional[dict]:
    """
    Descarga la serie NAV completa desde 1990 para determinar
    el rango de fechas disponible.
    """
    try:
        nav_data = fund.nav(
            start_date = datetime(1990, 1, 1),
            end_date   = datetime.today(),
            frequency  = NAV_FREQUENCY,
        )
        if not nav_data:
            return None
        dates = [str(r["date"])[:10] for r in nav_data if r.get("nav") is not None]
        if not dates:
            return None
        return {
            "first_date": min(dates),
            "last_date":  max(dates),
            "nav_count":  len(dates),
            "data":       nav_data,   # conservamos para no repetir la descarga
        }
    except Exception:
        return None


def _resample_to_monthly(rows: list[dict]) -> list[dict]:
    """
    De una serie diaria, conserva solo el ultimo NAV de cada mes.
    Equivalente a un resample mensual a fin de mes.
    """
    # Agrupar por YYYY-MM, quedarse con la fecha mas alta de cada grupo
    monthly: dict[str, dict] = {}
    for row in rows:
        key = row["Date"][:7]   # YYYY-MM
        if key not in monthly or row["Date"] > monthly[key]["Date"]:
            monthly[key] = row
    return sorted(monthly.values(), key=lambda r: r["Date"])


# ============================================================
# Descarga NAV historico
# ============================================================

def _download_nav(code: str, isin: str, currency: str, desde: str):
    """
    Descarga la serie de retorno total mensual via el endpoint
    sal-service/v1/fund/performance/v4/{code} (acceso directo HTTP,
    sin mstarpy.Funds constructor, sin bearer token de scraping).

    Parametros:
        code:     securityID interno de Morningstar (ej. 'F000011KV2')
        isin:     ISIN del fondo (para rellenar el campo en las filas)
        currency: divisa del fondo
        desde:    no usado (el endpoint devuelve historico completo ~10 anos)

    Devuelve (rows, err_type):
        rows:     lista de dicts para fund_nav_monthly
        err_type: '' OK | 'empty' sin datos | 'transient' error de red
    """
    url     = _MS_PERF_URL.format(code=code)
    headers = {
        "apikey":     _MS_APIKEY,
        "user-agent": _random_ua(),
    }
    for attempt in range(1, MS_RETRY_MAX + 1):
        try:
            r = requests.get(url, params=_MS_PERF_PARAMS,
                             headers=headers, timeout=20)
            if r.status_code == 206:
                # 206 = code no reconocido por sal-service
                return [], "empty"
            if r.status_code != 200:
                err_str = str(r.status_code)
                is_transient = err_str in ("429", "500", "502", "503")
                if is_transient and attempt < MS_RETRY_MAX:
                    wait = MS_BACKOFF_BASE * (MS_BACKOFF_FACTOR ** (attempt - 1))
                    wait += random.uniform(0, wait * 0.2)
                    print(f"\n    [red transitoria {r.status_code}] intento "
                          f"{attempt}/{MS_RETRY_MAX} -- esperando {wait:.0f}s...",
                          flush=True)
                    time.sleep(wait)
                    continue
                return [], "transient"

            hd    = r.json()
            serie = hd.get("graphData", {}).get("fund", [])
            if not serie:
                return [], "empty"

            base_currency = hd.get("baseCurrency") or currency or "EUR"
            rows = []
            for entry in serie:
                val      = entry.get("value")
                nav_date = entry.get("date")
                if val is None or nav_date is None:
                    continue
                rows.append({
                    "ISIN":         isin,
                    "Date":         str(nav_date)[:10],
                    "NAV":          float(val),
                    "NAV_Currency": base_currency,
                    "NAV_Type":     "TOTAL_RETURN_IDX",
                    "Is_Estimated": 0,
                    "Data_Source":  "MORNINGSTAR",
                })
            # Devuelve filas DIARIAS (sin resamplear). El llamante resamplea a
            # mensual para fund_nav_monthly y persiste la serie diaria en
            # fund_nav_daily (v24 — métricas de horizonte corto).
            return rows, ""

        except requests.RequestException as e:
            err_str = str(e).lower()
            is_transient = any(x in err_str for x in [
                "timed out", "timeout", "connection", "dns", "name or service"
            ])
            if is_transient and attempt < MS_RETRY_MAX:
                wait = MS_BACKOFF_BASE * (MS_BACKOFF_FACTOR ** (attempt - 1))
                wait += random.uniform(0, wait * 0.2)
                print(f"\n    [red transitoria] intento {attempt}/{MS_RETRY_MAX}"
                      f" -- esperando {wait:.0f}s...", flush=True)
                time.sleep(wait)
            else:
                return [], "transient"

    return [], "transient"


def _get_bearer_token_browser() -> str | None:
    """Fallback: obtiene el bearer JWT via Playwright (headless Chrome).

    Estrategia doble:
    1. Interceptar la primera peticion a chartservice en la carga de pagina
    2. Si no se intercepta, extraer el token del HTML renderizado (misma
       logica que mstarpy.token_chart pero despues de ejecutar JS)

    Retorna None si Playwright no esta disponible o no se encontro el token.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    token_holder: dict = {"token": None}

    def _on_request(request) -> None:
        if token_holder["token"]:
            return
        if "chartservice" in request.url:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token_holder["token"] = auth[7:]

    import re as _re

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            # Ocultar webdriver flag (evitar deteccion headless)
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            page = ctx.new_page()
            page.on("request", _on_request)
            # Navegar y esperar JS completo (JWT se inyecta via JS, no SSR)
            try:
                page.goto(
                    "https://www.morningstar.com/funds/xnas/afozx/chart",
                    wait_until="networkidle",
                    timeout=75_000,
                )
            except Exception:
                pass  # timeout OK; el HTML parcial puede tener el JWT

            # Scroll + click para forzar carga del chart si aun no se capturo
            if not token_holder["token"]:
                try:
                    page.evaluate("window.scrollTo(0, 500)")
                    page.wait_for_timeout(5_000)
                except Exception:
                    pass

            # Estrategia 2: buscar JWT en el HTML renderizado
            if not token_holder["token"]:
                try:
                    html = page.content()
                    jwt_match = _re.search(
                        r'ey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',
                        html,
                    )
                    if jwt_match:
                        token_holder["token"] = jwt_match.group(0)
                except Exception:
                    pass

            # Estrategia 3: extraer JWT desde los scripts inline de la pagina
            if not token_holder["token"]:
                try:
                    token_from_js = page.evaluate("""
                        () => {
                            const all = Array.from(
                                document.querySelectorAll('script')
                            ).map(s => s.textContent || '').join(' ');
                            const m = all.match(
                                /ey[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}/
                            );
                            return m ? m[0] : null;
                        }
                    """)
                    if token_from_js:
                        token_holder["token"] = token_from_js
                except Exception:
                    pass

            browser.close()
    except Exception as e:
        print(f"  [browser] Playwright error: {e}", flush=True)
        return None

    return token_holder["token"]


def _get_bearer_token() -> str:
    """Obtiene el bearer token para chartservice.

    Estrategia en cascada:
    1. token_chart() (scraping rapido, ~1s)
    2. _get_bearer_token_browser() (Playwright headless, ~5-10s) — bypasa bot-detection
    Si ambos fallan, lanza RuntimeError.
    """
    token = _ms_token_chart()
    if token:
        return token

    print("  [token] scraping rapido fallo (bot-detection?) -> intentando via browser...")
    token = _get_bearer_token_browser()
    if token:
        print("  [token] Bearer obtenido via Playwright (browser).")
        return token

    raise RuntimeError(
        "No se pudo obtener el bearer token para chartservice.\n"
        "Tanto token_chart() como Playwright fallaron.\n\n"
        "Fallback manual: pasa el token con --bearer-token <TOKEN>\n"
        "(Obtenlo desde DevTools -> Network -> Authorization header)"
    )


def _download_nav_daily(
    code: str,
    isin: str,
    currency: str,
    desde: str,
    bearer: str,
) -> tuple[list[dict], str, str | None]:
    """
    Descarga la serie de totalReturn DIARIA via chartservice/v2/timeseries.

    Usa el bearer JWT de mstarpy.security.token_chart — no requiere Funds().
    El code (securityID) se lee de nav_sources.source_id, ya resuelto en discover.

    Parametros:
        code:     securityID de Morningstar (ej. 'F0GBR069U8')
        isin:     ISIN del fondo
        currency: divisa del fondo (para el campo NAV_Currency)
        desde:    fecha de inicio ISO (ej. '2000-01-01')
        bearer:   token JWT actual; se pasa para poder renovarlo sin reescribir

    Devuelve (rows, err_type, new_bearer):
        rows:       lista de dicts diarios — campo NAV = totalReturn index
        err_type:   '' OK | 'empty' sin datos | 'transient' red | 'auth' 401
        new_bearer: None si el token sigue valido; str con token nuevo si hubo 401
    """
    headers = {
        "user-agent":    _ms_random_ua(),
        "authorization": f"Bearer {bearer}",
    }
    params = {
        "query":           f"{code}:totalReturn",
        "frequency":       "d",
        "startDate":       desde,
        "endDate":         date.today().isoformat(),
        "trackMarketData": "3.6.3",
        "instid":          "DOTCOM",
    }

    for attempt in range(1, _CHART_RETRY_MAX + 1):
        try:
            r = requests.get(_MS_CHART_URL, params=params,
                             headers=headers, timeout=20)

            if r.status_code == 401:
                # No refrescamos aqui: puede ser token caducado O security inaccesible.
                # El llamante decide segun la edad del token (evita Playwright en masa).
                return [], "auth", None

            if r.status_code != 200:
                if r.status_code == 429 and attempt < _CHART_RETRY_MAX:
                    print(f"\n    [chart 429] rate-limit, esperando {_CHART_BACKOFF_429}s...",
                          flush=True)
                    time.sleep(_CHART_BACKOFF_429)
                    continue
                if r.status_code in (500, 502, 503) and attempt < _CHART_RETRY_MAX:
                    print(f"\n    [chart {r.status_code}] error servidor, esperando {_CHART_BACKOFF_OTHER}s...",
                          flush=True)
                    time.sleep(_CHART_BACKOFF_OTHER)
                    continue
                return [], "transient", None

            data = r.json()
            if not data or "series" not in data[0]:
                return [], "empty", None

            series = data[0]["series"]
            rows = []
            for entry in series:
                # totalReturn preferido; nav como fallback
                val      = entry.get("totalReturn") or entry.get("nav")
                nav_date = entry.get("date")
                if val is None or nav_date is None:
                    continue
                rows.append({
                    "ISIN":         isin,
                    "Date":         str(nav_date)[:10],
                    "NAV":          float(val),
                    "NAV_Currency": currency or "EUR",
                    "NAV_Type":     "TOTAL_RETURN_IDX",
                    "Is_Estimated": 0,
                    "Data_Source":  "MORNINGSTAR_CHART",
                })
            return rows, "", None

        except requests.RequestException as e:
            err_str = str(e).lower()
            is_transient = any(x in err_str for x in [
                "timed out", "timeout", "connection", "dns", "name or service"
            ])
            if is_transient and attempt < MS_RETRY_MAX:
                wait = MS_BACKOFF_BASE * (MS_BACKOFF_FACTOR ** (attempt - 1))
                wait += random.uniform(0, wait * 0.2)
                print(f"\n    [chart red] intento {attempt}/{MS_RETRY_MAX}"
                      f" -- esperando {wait:.0f}s...", flush=True)
                time.sleep(wait)
            else:
                return [], "transient", None

    return [], "transient", None


# ============================================================
# Escritura en DB
# ============================================================

def _write_nav_source(
    conn, isin, source, source_id,
    first_date, last_date, nav_count, status, dry_run
):
    if dry_run:
        return
    today = date.today().isoformat()
    if is_postgres_connection(conn):
        # NOTA (encontrado en vivo 2026-09-20): a diferencia de SQLite, donde un
        # nombre de columna sin cualificar en DO UPDATE SET resuelve sin ambiguedad
        # a la fila actual de la tabla destino, Postgres lanza
        # psycopg.errors.AmbiguousColumn si esa misma columna tambien existe en
        # `excluded` -- hay que cualificar explicitamente con el nombre de tabla.
        conn.execute("""
            INSERT INTO nav_sources
                (isin, source, source_id, first_nav_date, last_nav_date,
                 nav_count, discovered_at, last_checked, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (isin) DO UPDATE SET
                source         = excluded.source,
                -- P#1 COALESCE: nunca borrar un code ya resuelto (no regresar OK->NOT_FOUND)
                source_id      = COALESCE(NULLIF(excluded.source_id, ''), nav_sources.source_id),
                first_nav_date = COALESCE(excluded.first_nav_date, nav_sources.first_nav_date),
                last_nav_date  = COALESCE(excluded.last_nav_date,  nav_sources.last_nav_date),
                nav_count      = COALESCE(excluded.nav_count,      nav_sources.nav_count),
                last_checked   = excluded.last_checked,
                -- Solo degradar a NOT_FOUND si no hay code resuelto previo
                status         = CASE
                    WHEN excluded.status = 'NOT_FOUND'
                     AND nav_sources.source_id IS NOT NULL AND nav_sources.source_id != ''
                    THEN nav_sources.status  -- mantener estado actual (OK)
                    ELSE excluded.status     -- actualizar normalmente
                END
        """, (isin, source, source_id, first_date, last_date,
              nav_count, today, today, status))
    else:
        conn.execute("""
            INSERT INTO nav_sources
                (isin, source, source_id, first_nav_date, last_nav_date,
                 nav_count, discovered_at, last_checked, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(isin) DO UPDATE SET
                source         = excluded.source,
                -- P#1 COALESCE: nunca borrar un code ya resuelto (no regresar OK->NOT_FOUND)
                source_id      = COALESCE(NULLIF(excluded.source_id, ''), source_id),
                first_nav_date = COALESCE(excluded.first_nav_date, first_nav_date),
                last_nav_date  = COALESCE(excluded.last_nav_date,  last_nav_date),
                nav_count      = COALESCE(excluded.nav_count,      nav_count),
                last_checked   = excluded.last_checked,
                -- Solo degradar a NOT_FOUND si no hay code resuelto previo
                status         = CASE
                    WHEN excluded.status = 'NOT_FOUND'
                     AND source_id IS NOT NULL AND source_id != ''
                    THEN status          -- mantener estado actual (OK)
                    ELSE excluded.status -- actualizar normalmente
                END
        """, (isin, source, source_id, first_date, last_date,
              nav_count, today, today, status))
    conn.commit()


def _normalize_nav_scale(rows: list) -> list:
    """FIX-P2-NAV-SCALE-1 (2026-07-19): detecta y corrige mezcla de escalas NAV.

    Dos patrones de corrupción se han observado en fund_nav_monthly:

    Patrón A — mezcla de fuentes en bloque: MORNINGSTAR (sal-service, todos los
      rows a escala ~9 000x) + MORNINGSTAR_CHART (chartservice, todos a escala ~9x).
      La mediana de una fuente difiere de la otra en un factor limpio de 10^n ≥ 100.
      Corrección: rescalar los rows de la fuente inflada → dividir por 10^n.

    Patrón B — picos aislados dentro de una misma fuente: MORNINGSTAR devuelve
      en ocasiones el índice de retorno total (acumulado desde el inicio) en lugar
      del NAV de precio para las fechas de cierre de calendario. Estos rows tienen
      NAV ~×100 respecto al período adyacente (por ejemplo, NAV 87.92 el día 29
      de enero frente a NAV 9291.90 el día 31 de enero del mismo mes).
      Corrección: eliminar los picos aislados (rows cuyo NAV > 8× el anterior Y
      > 8× el siguiente cuando hay suficiente contexto).

    Ambos pases son idempotentes. La función aplica primero el filtro de picos
    (Patrón B) y luego el rescalado por fuente (Patrón A).

    Parámetros:
        rows: lista de dicts con claves "NAV", "Date" y "Data_Source" (al menos)

    Devuelve lista de dicts (subconjunto o con NAVs corregidos).
    """
    if len(rows) < 2:
        return rows

    import math
    import statistics as _stats

    # ── Patrón B: eliminar picos aislados ──────────────────────────────────────
    # Un "pico" es un row tal que su NAV > 8× el anterior Y > 8× el siguiente
    # (contexto interior), o > 8× el único vecino disponible (bordes).
    if len(rows) >= 3:
        _sorted = sorted(rows, key=lambda r: r["Date"])
        _navs   = [r["NAV"] for r in _sorted]
        _keep   = [True] * len(_navs)

        for i in range(1, len(_navs) - 1):
            if _navs[i] > 0 and _navs[i-1] > 0 and _navs[i+1] > 0:
                if _navs[i] > 8 * _navs[i-1] and _navs[i] > 8 * _navs[i+1]:
                    _keep[i] = False  # pico interior aislado

        # Pico en borde final (e.g. último row del lote es un valor inflado)
        if _navs[-1] > 0 and _navs[-2] > 0 and _navs[-1] > 8 * _navs[-2]:
            _keep[-1] = False

        rows = [r for r, k in zip(_sorted, _keep) if k]
        if len(rows) < 2:
            return rows

    # ── Patrón A: rescalar fuente inflada frente a MORNINGSTAR_CHART ──────────
    chart_navs = [r["NAV"] for r in rows
                  if r.get("Data_Source") == "MORNINGSTAR_CHART" and r["NAV"] > 0]
    other_navs = [r["NAV"] for r in rows
                  if r.get("Data_Source") != "MORNINGSTAR_CHART" and r["NAV"] > 0]

    if not chart_navs or not other_navs:
        return rows

    ref_median   = _stats.median(chart_navs)   # escala de referencia (CHART = limpia)
    other_median = _stats.median(other_navs)

    if ref_median <= 0 or other_median <= 0:
        return rows

    ratio = other_median / ref_median
    if ratio <= 0:
        return rows

    log10_ratio = math.log10(ratio)
    n = round(log10_ratio)

    # Solo corregir si la diferencia es un múltiplo limpio de 10^n con n ≥ 2
    # (es decir, al menos ×100). Tolerancia ±0.2 décadas (≈ ×63 a ×158 para n=2).
    # Requerimos n ≥ 2 para no tocar diferencias de ×10 (p.ej. divisa o clase).
    if n < 2 or abs(log10_ratio - n) > 0.2:
        return rows  # diferencia real o escala mixta no reconocida → no tocar

    scale_factor = 10 ** n  # la fuente no-CHART está inflada en este factor

    corrected = []
    for r in rows:
        if r.get("Data_Source") == "MORNINGSTAR_CHART":
            corrected.append(r)
        else:
            r2 = r.copy()
            r2["NAV"] = round(r["NAV"] / scale_factor, 6)
            corrected.append(r2)
    return corrected


def _splice_new_chart_batch(conn, isin: str, rows: list, jump_threshold: float = 8.0) -> list:
    """Re-anchor a freshly-fetched MORNINGSTAR_CHART batch onto existing
    history before it is ever written, so chartservice's per-fetch arbitrary
    rebasing can't introduce a new discontinuity.

    Durable ingestion-time counterpart of
    scripts/mig/repair_nav_scale_20260719.py::_splice_rebased_index_batches()
    (the retroactive/reference implementation — same carry-factor idea).
    That script has to walk a whole ISIN's history rescaling every batch
    boundary it finds after the fact; this only ever needs to handle ONE
    boundary per call, because each nav_discovery write already corresponds
    to exactly one fetched batch — so it re-anchors just the incoming rows
    against whatever's already stored, instead of re-deriving the whole
    chain.

    Root cause this closes (2026-09-13/14/15, see
    project_nav_scale_rebasing_fix_20260913 / project_p0_p2_canonicalization
    session memory): the splice fix had only ever been applied retroactively
    — a routine NAV Load re-introduced the identical seam on the same 3
    ISINs hours after being spliced, proving the corruption isn't a rare
    edge case but recurs on ordinary operational runs. validate_nav()'s >8x
    guard was catching it and skipping cleanly (no corrupted fund_metrics),
    but that just meant those funds silently stopped getting recomputed
    every time the chartservice happened to re-rebase.

    Anchor selection: prefers an EXACT same-date overlap between the new
    batch and existing fund_nav_daily history (chartservice windows often
    overlap on re-fetch) — the most reliable comparison, since it's the same
    real calendar date observed twice. Falls back to the nearest existing
    date strictly before the new batch's earliest date when there is no
    overlap. No existing MORNINGSTAR_CHART history at all (first-ever load)
    -> nothing to splice against, rows returned unchanged.

    Only rescales when the ratio at the anchor exceeds jump_threshold (or
    its inverse, matching the retroactive script's own threshold) — an
    ordinary day-to-day price move is left untouched. Applies uniformly to
    every MORNINGSTAR_CHART row in the batch (the same carry-factor
    correction the retroactive splice uses), non-chart rows pass through.
    """
    if not rows:
        return rows
    new_by_date = {
        r["Date"]: r["NAV"] for r in rows
        if r.get("Data_Source") == "MORNINGSTAR_CHART" and r.get("NAV") and r["NAV"] > 0
    }
    if not new_by_date:
        return rows
    new_min_date = min(new_by_date)

    overlap_dates = list(new_by_date.keys())
    ph = ",".join("?" * len(overlap_dates))
    overlap = conn.execute(
        f"SELECT Date, NAV FROM fund_nav_daily WHERE ISIN=? "
        f"AND Data_Source='MORNINGSTAR_CHART' AND NAV > 0 AND Date IN ({ph}) "
        f"ORDER BY Date LIMIT 1",
        (isin, *overlap_dates),
    ).fetchone()

    if overlap:
        anchor_date, anchor_existing_nav = overlap
        anchor_new_nav = new_by_date[anchor_date]
    else:
        prior = conn.execute(
            "SELECT Date, NAV FROM fund_nav_daily WHERE ISIN=? "
            "AND Data_Source='MORNINGSTAR_CHART' AND NAV > 0 AND Date < ? "
            "ORDER BY Date DESC LIMIT 1",
            (isin, new_min_date),
        ).fetchone()
        if not prior:
            return rows  # no history to splice against (first load, or huge gap)
        anchor_date, anchor_existing_nav = prior
        anchor_new_nav = new_by_date[new_min_date]

    if anchor_new_nav <= 0 or anchor_existing_nav <= 0:
        return rows
    ratio = anchor_new_nav / anchor_existing_nav
    if not (ratio >= jump_threshold or ratio <= 1.0 / jump_threshold):
        return rows  # continuous — no seam at this boundary

    carry_factor = anchor_existing_nav / anchor_new_nav
    spliced = []
    for r in rows:
        if r.get("Data_Source") == "MORNINGSTAR_CHART" and r.get("NAV"):
            r2 = r.copy()
            r2["NAV"] = round(r["NAV"] * carry_factor, 6)
            spliced.append(r2)
        else:
            spliced.append(r)
    return spliced


def _write_nav_rows(conn, rows, dry_run) -> int:
    """Persiste filas NAV mensuales en fund_nav_monthly (INSERT OR IGNORE).

    FIX-NAV-OPEN-MONTH-1 (2026-09-13): la clave semantica real de una fila
    mensual es (ISIN, YYYY-MM), pero la PK de la tabla es (ISIN, Date). Sin
    este DELETE previo, cada ejecucion de ingesta durante un mes aun abierto
    inserta una fila con una Date distinta (el ultimo dia disponible en ese
    momento) en vez de sustituir la fila provisional anterior del mismo mes
    -> el mes abierto acumula varias filas, y ademas -- por ser OR IGNORE y
    no OR REPLACE -- la primera fila escrita para un mes queda fija para
    siempre incluso despues de cerrarse el mes con un valor mas correcto.
    Verificado en produccion: 6.876 pares (ISIN, mes) con filas duplicadas
    (13.308 filas sobrantes), 99% concentradas en los 2 meses mas recientes.
    Ver doc/reglas/AUDITORIA_ESTADISTICA.md §2.7 y
    scripts/mig/fix_nav_monthly_duplicate_months.py para el saneado de datos
    ya corrompidos (no ejecutado automaticamente por este fix).
    """
    if not rows or dry_run:
        return 0
    # FIX-P2-NAV-SCALE-1 (2026-07-19): normalizar escala antes de persistir.
    # Sin esto, mezclar rows MORNINGSTAR (~9000x) con MORNINGSTAR_CHART (~9x)
    # produce retornos fantasma que corrompen srri_nav → 7 para fondos defensivos.
    rows = _normalize_nav_scale(rows)

    # Limpiar cualquier fila previa del mismo (ISIN, YYYY-MM) antes de insertar
    # la nueva -- misma logica que _overwrite_nav_rows_monthly() pero acotada
    # a los meses que realmente se estan escribiendo, no todo el historico del ISIN.
    months = {(r["ISIN"], r["Date"][:7]) for r in rows}
    if is_postgres_connection(conn):
        # Date es tipo `date` en Postgres (no text) -- substr() necesita el cast explicito.
        executemany(
            conn,
            "DELETE FROM fund_nav_monthly WHERE isin=%s AND substr(date::text,1,7)=%s",
            list(months),
        )
        executemany(conn, """
            INSERT INTO fund_nav_monthly
                (isin, date, nav, nav_currency, nav_type, is_estimated, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (isin, date) DO NOTHING
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    else:
        executemany(
            conn,
            "DELETE FROM fund_nav_monthly WHERE ISIN=? AND substr(Date,1,7)=?",
            list(months),
        )
        executemany(conn, """
            INSERT OR IGNORE INTO fund_nav_monthly
                (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    # No commit aqui — el llamante agrupa commits por lote para reducir fsyncs
    return len(rows)


def _write_nav_rows_daily(conn, rows, dry_run) -> int:
    """Persiste filas NAV diarias en fund_nav_daily.

    v24: fund_nav_daily es la serie pre-resample usada por
    proyecto2/src/calculations/short_horizon.py para métricas
    rolling_1m / rolling_3m / rolling_6m (metric_version='d1').

    Limpia primero las filas del endpoint antiguo (sal-service, escala ~9k-30k)
    que no coinciden en fecha con chartservice (~300) — mezclarlas produce
    retornos fantasma del -99% que corrompen todas las métricas diarias.
    """
    if not rows or dry_run:
        return 0
    isin = rows[0]["ISIN"]
    if is_postgres_connection(conn):
        conn.execute(
            "DELETE FROM fund_nav_daily WHERE isin=%s AND data_source != 'MORNINGSTAR_CHART'",
            (isin,),
        )
        executemany(conn, """
            INSERT INTO fund_nav_daily
                (isin, date, nav, nav_currency, nav_type, is_estimated, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (isin, date) DO UPDATE SET
                nav          = excluded.nav,
                nav_currency = excluded.nav_currency,
                nav_type     = excluded.nav_type,
                is_estimated = excluded.is_estimated,
                data_source  = excluded.data_source
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    else:
        conn.execute(
            "DELETE FROM fund_nav_daily WHERE ISIN=? AND Data_Source != 'MORNINGSTAR_CHART'",
            (isin,),
        )
        executemany(conn, """
            INSERT OR REPLACE INTO fund_nav_daily
                (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    # No commit aqui — el llamante agrupa commits por lote para reducir fsyncs
    return len(rows)


# ============================================================
# v25 — Helpers de estado NAV (data_status state machine)
# ============================================================

def _ensure_data_status_column(conn) -> None:
    """Migración idempotente v25: añade data_status a nav_sources si no existe."""
    if is_postgres_connection(conn):
        # Postgres soporta ADD COLUMN IF NOT EXISTS de forma nativa -- no hace falta
        # comprobar la columna antes (el target schema ya la define; esto es un no-op).
        conn.execute("ALTER TABLE nav_sources ADD COLUMN IF NOT EXISTS data_status TEXT DEFAULT 'OK'")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nav_sources_data_status ON nav_sources (data_status)"
        )
        conn.commit()
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(nav_sources)").fetchall()}
    if "data_status" not in cols:
        conn.execute("""
            ALTER TABLE nav_sources
            ADD COLUMN data_status TEXT DEFAULT 'OK'
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_nav_sources_data_status
            ON nav_sources (data_status)
        """)
        conn.commit()
        print("  [v25] Migración aplicada: data_status añadido a nav_sources.", flush=True)


def _overwrite_nav_rows_monthly(conn, isin: str, rows: list[dict], dry_run: bool) -> int:
    """DELETE los mensuales existentes del ISIN e INSERT los nuevos.

    Usado en RECALCULATE_MONTHLY: el daily es la fuente de verdad; el mensual
    se regenera limpiamente desde él. INSERT OR IGNORE no serviría aquí porque
    los rows ya existen (se quiere sobreescribirlos con la corrección).
    """
    if not rows or dry_run:
        return len(rows) if dry_run else 0
    rows = _normalize_nav_scale(rows)
    if is_postgres_connection(conn):
        conn.execute("DELETE FROM fund_nav_monthly WHERE isin=%s", (isin,))
        executemany(conn, """
            INSERT INTO fund_nav_monthly
                (isin, date, nav, nav_currency, nav_type, is_estimated, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    else:
        conn.execute("DELETE FROM fund_nav_monthly WHERE ISIN=?", (isin,))
        executemany(conn, """
            INSERT INTO fund_nav_monthly
                (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
               r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    return len(rows)


# ============================================================
# Modo DISCOVER
# ============================================================

def run_discover(conn, isins, dry_run, verbose, skip_if_recent: bool = False):
    """
    Verifica existencia del ISIN en Morningstar y registra el code interno
    en nav_sources. Usa _resolve_isin() (general_search directo) para evitar
    mstarpy.Funds() y el endpoint /data-points/fields que devuelve 202.

    skip_if_recent: si True y >=90% de los ISINs tienen last_checked=hoy,
        omite la ejecucion (evita re-lanzar los ~2h de discover tras un
        corte en mid-load). Sobreescribir con --force.
    """
    if skip_if_recent and not dry_run:
        today_s = date.today().isoformat()
        recent  = conn.execute(
            "SELECT COUNT(*) FROM nav_sources WHERE last_checked >= ?",
            (today_s,)
        ).fetchone()[0]
        if recent >= len(isins) * 0.90:
            print(
                f"[DISCOVER] Omitido — {recent}/{len(isins)} ISINs ya comprobados hoy "
                f"({today_s}). Usa --force para forzar.", flush=True
            )
            return

    total     = len(isins)
    found     = 0
    not_found = 0
    errors    = 0

    print(f"Descubrimiento de {total} ISINs | dry_run={dry_run}\n")

    for idx, isin in enumerate(isins, 1):
        _t0_disc = time.perf_counter()

        resolved = _resolve_isin(isin)
        time.sleep(random.uniform(*MS_DELAY_DISCOVER))

        # Reintentar una vez si el resultado es challenge o none
        if resolved is None or (isinstance(resolved, dict) and resolved.get("challenge")):
            resolved = _resolve_isin(isin)
            time.sleep(random.uniform(*MS_DELAY_DISCOVER))

        _dur_ms = round((time.perf_counter() - _t0_disc) * 1000)
        _ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(resolved, dict) and resolved.get("challenge"):
            print(f"[{idx:4d}/{total:4d}] | {_ts} | {isin} | SKIP | [] |  | {_dur_ms}",
                  flush=True)
            errors += 1
            continue

        if resolved is None:
            print(f"[{idx:4d}/{total:4d}] | {_ts} | {isin} | NOT_FOUND | [] |  | {_dur_ms}",
                  flush=True)
            _write_nav_source(conn, isin, "MORNINGSTAR", "",
                              None, None, None, "NOT_FOUND", dry_run)
            not_found += 1
            continue

        code = resolved["code"]
        name = resolved["name"]
        print(f"[{idx:4d}/{total:4d}] | {_ts} | {isin} | OK | [{name[:60]}] | {code} | {_dur_ms}",
              flush=True)
        _write_nav_source(conn, isin, "MORNINGSTAR", code,
                          None, None, None, "OK", dry_run)
        found += 1

    _print_summary(found, not_found, errors, total, dry_run)


# ============================================================
# Modo LOAD
# ============================================================

def _effective_cutoff(today: date, stale_days: int = 3) -> date:
    """Cutoff para la comprobacion "al dia", ajustado por fin de semana.

    El proveedor publica el cierre del viernes con 1-2 dias de latencia.
    Sin ajuste, los fondos con anchor=viernes fallan la comprobacion el
    lunes (hoy-3=viernes misma fecha) o el martes (hoy-3=sabado) porque
    el anchor es anterior al cutoff, provocando re-fetch innecesario.

    Lunes -> extender 2 dias extra (ultimo cierre = viernes 3 dias atras)
    Domingo -> extender 1 dia extra
    Resto -> ventana estandar de `stale_days` dias

    `stale_days` (default 3) es la ventana base de tolerancia diaria. DEBE
    alinearse con la cadencia real del job: una ventana de 3 dias con una
    cadencia semanal/mensual marca ~100% del universo como stale en cada
    ejecucion (el anchor de todo el libro siempre queda por detras del cutoff).
    Ver `--stale-days` en modo update.
    """
    wd    = today.weekday()  # 0=lunes, 6=domingo
    extra = 2 if wd == 0 else (1 if wd == 6 else 0)
    return today - timedelta(days=stale_days + extra)


def _latest_published_month_end(today: date, latency_days: int = 5) -> date:
    """Ultimo cierre de mes COMPLETADO y presumiblemente publicado.

    El NAV mensual es fin-de-mes. A mitad de mes el cierre del mes en curso
    aun no existe, asi que la referencia de "al dia" en grano mensual es el
    ultimo dia del mes anterior. Si estamos en los primeros `latency_days`
    del mes, ese cierre puede no estar publicado todavia -> retroceder un mes
    mas para no re-descargar todo el universo por latencia del proveedor.
    """
    first_this = today.replace(day=1)
    prev_month_end = first_this - timedelta(days=1)          # ultimo dia mes anterior
    if (today - first_this).days < latency_days:
        return prev_month_end.replace(day=1) - timedelta(days=1)  # un mes mas atras
    return prev_month_end


def _is_stale(ds: str, last_daily: Optional[str], last_monthly: Optional[str],
              cutoff: date, ref_month_end: date, monthly_grain: bool) -> bool:
    """Decide si un fondo necesita descarga (True = stale).

    Fuente unica de verdad para el gate "al dia", usada tanto por el
    pre-filtro a nivel de conjunto como por el gate por-fondo (DRY — P#11).

    - FORCE_REFRESH / RECALCULATE_MONTHLY -> siempre se procesan.
    - STALE_FROZEN -> nunca se procesan (fuente estructuralmente detenida;
      sancionada, suspendida, o dada de baja por el proveedor). El fondo
      permanece en nav_sources con status='OK' para conservar el ms_id,
      pero no se realizan llamadas de red hasta que el operador revierta
      data_status a 'OK' manualmente.
    - monthly_grain=True  -> stale si el ultimo NAV mensual (`nav_sources.
      last_nav_date`) no cubre `ref_month_end`.
    - monthly_grain=False -> stale si el ancla diaria (`fund_nav_daily` MAX)
      es anterior a `cutoff`.
    Sin dato previo -> stale (carga inicial).
    """
    if ds in ("FORCE_REFRESH", "RECALCULATE_MONTHLY"):
        return True
    if ds == "STALE_FROZEN":
        return False  # never attempt downloads on a structurally frozen source
    if monthly_grain:
        if not last_monthly:
            return True
        try:
            return datetime.strptime(last_monthly[:10], "%Y-%m-%d").date() < ref_month_end
        except (ValueError, TypeError):
            return True
    if not last_daily:
        return True
    try:
        return datetime.strptime(last_daily[:10], "%Y-%m-%d").date() < cutoff
    except (ValueError, TypeError):
        return True


def _auto_freeze_stale_navs(conn, dry_run: bool) -> list:
    """Detect structurally-stopped NAV sources and mark them STALE_FROZEN.

    Extracted 2026-09-15 from run_update() (the only caller until now) so
    run_load() — the mode the canonical launcher (P2_discoverLoadMetrics.bat)
    actually runs every cycle — can call it too. Root cause of FIX-FROZEN-NAV
    being dead code in production for 3+ weeks: this detection lived only
    inside run_update(), which the launcher never invokes (only --mode
    discover and --mode load run); _is_stale() already correctly skips
    STALE_FROZEN sources wherever it's consulted, so the only missing piece
    was something ever setting the status on a path that actually executes.

    A fund qualifies when its source has gone quiet (no last_checked update
    in 30 days would mean discover/load themselves stopped trying, which is
    a different problem) but genuinely has no new NAV in > _FROZEN_NAV_DAYS
    despite recent checks — i.e. the provider stopped publishing (sanction,
    suspension, delisting), not that our own pipeline stopped looking.

    Returns the list of (isin, last_nav_date, last_checked) rows just frozen
    (empty if none). Does nothing on dry_run (no write should happen).
    """
    if dry_run:
        return []
    _today = date.today()
    _freeze_threshold = (_today - timedelta(days=_FROZEN_NAV_DAYS)).isoformat()
    _recent_check_threshold = (_today - timedelta(days=30)).isoformat()
    _ph = "%s" if is_postgres_connection(conn) else "?"
    _to_freeze = conn.execute(
        f"""
        SELECT isin, last_nav_date, last_checked
        FROM nav_sources
        WHERE status = 'OK'
          AND (data_status IS NULL OR data_status = 'OK')
          AND last_nav_date IS NOT NULL
          AND last_nav_date < {_ph}
          AND last_checked  >= {_ph}
        ORDER BY last_nav_date
        """,
        (_freeze_threshold, _recent_check_threshold),
    ).fetchall()
    if not _to_freeze:
        return []
    freeze_isins = [r[0] for r in _to_freeze]
    in_ph = ",".join([_ph] * len(freeze_isins))
    conn.execute(
        f"UPDATE nav_sources SET data_status='STALE_FROZEN' WHERE isin IN ({in_ph})",
        freeze_isins,
    )
    conn.commit()
    print(
        f"[STALE_FROZEN] {len(_to_freeze)} fondo(s) marcados: "
        + ", ".join(f"{r[0]} (last_nav={r[1]}, checked={r[2]})" for r in _to_freeze),
        flush=True,
    )
    print(
        "[STALE_FROZEN] ACCION REQUERIDA: revisar fund_master.In_Current_Universe "
        "para estos ISINs y ajustar a 0 si procede (dominio P1).",
        file=sys.stderr, flush=True,
    )
    return _to_freeze


def _fetch_one(idx, isin, ms_id, currency, eff_desde, bearer, delay_secs):
    """Worker puro para ThreadPoolExecutor — NINGUNA escritura en BD.

    Descarga la serie diaria, aplica el politeness delay, y devuelve
    el resultado para que el hilo principal lo persista.

    Parametros:
        delay_secs: tiempo de pausa post-descarga (0 o random.uniform(MS_DELAY_LOAD_OK))
    """
    t0 = datetime.now()
    rows, err, _ = _download_nav_daily(ms_id, isin, currency, eff_desde, bearer)
    if rows and delay_secs > 0:
        time.sleep(delay_secs)
    elapsed = (datetime.now() - t0).total_seconds()
    return idx, isin, rows, err, elapsed, eff_desde


def run_load(conn, isins, desde, dry_run, verbose, force=False, bearer_token=None,
             workers=1, stale_days=3):
    if isins:
        ph      = ",".join("?" * len(isins))
        db_rows = {r[0]: r[1] for r in conn.execute(
            f"SELECT isin, source_id FROM nav_sources "
            f"WHERE isin IN ({ph}) AND status='OK'", isins
        ).fetchall()}
        rows = [(isin, db_rows.get(isin, isin)) for isin in isins]
    else:
        rows = conn.execute(
            "SELECT isin, source_id FROM nav_sources "
            "WHERE status='OK' ORDER BY isin"
        ).fetchall()
        if not rows:
            print("No hay ISINs con status=OK en nav_sources.")
            print("Ejecuta primero: --mode discover")
            return

    if not rows:
        print("Nada que cargar. No hay ISINs con status=OK.")
        return

    # -- Preload: tres queries batch reemplazan O(N) queries dentro del loop --
    # newest chartservice date per ISIN (delta anchor)
    _today  = date.today()
    _cutoff = _effective_cutoff(_today, stale_days)  # "al dia" si last_stored >= cutoff
    last_daily = {r[0]: r[1] for r in conn.execute(
        "SELECT ISIN, MAX(Date) FROM fund_nav_daily "
        "WHERE Data_Source='MORNINGSTAR_CHART' GROUP BY ISIN"
    ).fetchall()}
    # currency map — elimina el SELECT por ISIN dentro del loop
    currency_map = {r[0]: (r[1] or "EUR") for r in conn.execute(
        "SELECT ISIN, Fund_Currency FROM fund_master"
    ).fetchall()}
    # Auto-freeze ANTES de leer data_status_map, para que los recien
    # congelados ya aparezcan como STALE_FROZEN en este mismo run (2026-09-15:
    # este era el gap — la deteccion solo vivia en run_update(), que el
    # lanzador canonico nunca invoca; ver _auto_freeze_stale_navs()).
    _auto_freeze_stale_navs(conn, dry_run)
    # data_status map — estado del ciclo de vida de los datos (v25)
    data_status_map = {r[0]: (r[1] or "OK") for r in conn.execute(
        "SELECT isin, data_status FROM nav_sources WHERE status='OK'"
    ).fetchall()}

    total         = len(rows)
    total_written = 0
    errors_load   = 0
    ok_count      = 0
    al_dia_count  = 0
    print(f"Procesando {total} fondos | desde={desde} | dry_run={dry_run}\n")

    # Reducir fsync para bulk load: NORMAL hace un fsync por checkpoint en lugar de
    # dos por commit (FULL). Seguro: en caso de crash solo perdemos el lote en curso,
    # que es idempotente (la proxima ejecucion lo reprocesa).
    _BATCH_COMMIT = 20          # commit cada N ISINs exitosos
    _batch_pending = 0
    if not dry_run and not is_postgres_connection(conn):
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -65536")  # 64 MB page cache

    # -- Bearer token para chartservice (obtenido una sola vez por run) -----
    if bearer_token:
        bearer = bearer_token
        print("  Bearer token chartservice suministrado via --bearer-token.\n")
    else:
        try:
            bearer = _get_bearer_token()
            print("  Bearer token chartservice obtenido.\n")
        except RuntimeError as e:
            print(f"\n  ERROR: {e}")
            print(f"[ERROR] Bearer token no disponible. Pipeline abortado.", file=sys.stderr, flush=True)
            return

    bearer_acquired_at = time.time()
    _TOKEN_MAX_AGE_S   = 45 * 60

    if workers > 1:
        # ----------------------------------------------------------------
        # Fase 1: pre-computar jobs (ms_id resolution + delta) en el hilo
        # principal (unico que puede escribir en la BD).
        # ----------------------------------------------------------------
        jobs = []
        for idx, (isin, ms_id) in enumerate(rows, 1):
            ds = data_status_map.get(isin, "OK") or "OK"

            # STALE_FROZEN: fuente estructuralmente detenida, nunca se procesa
            # (ver _is_stale() / _auto_freeze_stale_navs()) — sin red, sin escritura.
            if ds == "STALE_FROZEN":
                al_dia_count += 1
                print(f"  [{idx:>4}/{total}] {isin} -> STALE_FROZEN, omitido", flush=True)
                continue

            # RECALCULATE_MONTHLY: resamplear desde daily existente, sin red
            if ds == "RECALCULATE_MONTHLY":
                print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)
                _daily = conn.execute(
                    "SELECT ISIN, Date, NAV, NAV_Currency, NAV_Type, "
                    "Is_Estimated, Data_Source FROM fund_nav_daily "
                    "WHERE ISIN=? AND Data_Source='MORNINGSTAR_CHART' ORDER BY Date",
                    (isin,)
                ).fetchall()
                if not _daily:
                    print("-> sin datos diarios para recalcular", flush=True)
                    continue
                _monthly = _resample_to_monthly([dict(r) for r in _daily])
                _written = _overwrite_nav_rows_monthly(conn, isin, _monthly, dry_run)
                if not dry_run:
                    _ph = "%s" if is_postgres_connection(conn) else "?"
                    conn.execute(
                        f"UPDATE nav_sources SET data_status='OK', last_nav_date={_ph}, "
                        f"nav_count={_ph} WHERE isin={_ph}",
                        (_monthly[-1]["Date"] if _monthly else None, len(_monthly), isin)
                    )
                    _batch_pending += 1
                    if _batch_pending >= _BATCH_COMMIT:
                        conn.commit(); _batch_pending = 0
                print(f"-> {_written}m recalculados (sin red)", flush=True)
                ok_count += 1
                continue

            if not ms_id:
                resolved = _resolve_isin(isin)
                if resolved and resolved.get("code"):
                    ms_id = resolved["code"]
                    _ph = "%s" if is_postgres_connection(conn) else "?"
                    conn.execute(f"UPDATE nav_sources SET source_id={_ph} WHERE isin={_ph}",
                                 (ms_id, isin))
                    conn.commit()
            if not ms_id:
                print(f"  [ERR ] {isin} -> sin ms_id Morningstar")
                errors_load += 1
                continue

            currency      = currency_map.get(isin, "EUR")
            last_d_stored = last_daily.get(isin)
            force_this    = force or (ds == "FORCE_REFRESH")
            _t0_fund      = datetime.now()
            if last_d_stored and not force_this:
                anchor = datetime.strptime(last_d_stored, "%Y-%m-%d").date()
                if anchor >= _cutoff:
                    _elapsed_ms = round((datetime.now() - _t0_fund).total_seconds() * 1000)
                    _ts_skip    = _t0_fund.strftime("%Y-%m-%d %H:%M:%S")
                    print(
                        f"[{idx:4d}/{total:4d}] | {_ts_skip} | {isin} | "
                        f"0d/0m | [{anchor} -> {anchor}] | {_elapsed_ms} | Al dia",
                        flush=True,
                    )
                    al_dia_count += 1
                    continue
                eff_desde = (anchor - timedelta(days=3)).isoformat()
            else:
                eff_desde = desde
            jobs.append((idx, isin, ms_id, currency, eff_desde))

        print(f"  {al_dia_count} fondos al dia, {len(jobs)} a descargar "
              f"| workers={workers}\n", flush=True)

        # ----------------------------------------------------------------
        # Fase 2: descargas paralelas; el politeness delay va dentro del
        # worker (cada thread duerme independientemente).
        # ----------------------------------------------------------------
        auth_retry = []
        _delay = random.uniform(*MS_DELAY_LOAD_OK)

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(_fetch_one, j[0], j[1], j[2], j[3], j[4],
                          bearer, random.uniform(*MS_DELAY_LOAD_OK)): j
                for j in jobs
            }
            for fut in as_completed(futs):
                ridx, risin, rnav_rows, rerr, relas, reff_desde = fut.result()
                _ts_par = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if rerr == "auth":
                    job = futs[fut]
                    auth_retry.append((ridx, risin, job[2], job[3], reff_desde))
                    print("-> [401 — retry pendiente]", flush=True)
                    continue

                if not rnav_rows:
                    print(
                        f"[{ridx:4d}/{total:4d}] | {_ts_par} | {risin} | "
                        f"NO_DATA | [] | {round(relas * 1000)}",
                        flush=True,
                    )
                    errors_load += 1
                    continue

                # Escrituras en BD: solo hilo principal (SQLite single-writer)
                rnav_rows  = _splice_new_chart_batch(conn, risin, rnav_rows)
                _rl_d_wr   = _write_nav_rows_daily(conn, rnav_rows, dry_run)
                _rl_m_rows = _resample_to_monthly(rnav_rows)
                _rl_m_wr   = _write_nav_rows(conn, _rl_m_rows, dry_run)
                total_written += _rl_m_wr
                _dr_par = f"{rnav_rows[0]['Date']} -> {rnav_rows[-1]['Date']}"
                print(
                    f"[{ridx:4d}/{total:4d}] | {_ts_par} | {risin} | "
                    f"{_rl_d_wr}d/{_rl_m_wr}m | [{_dr_par}] | {round(relas * 1000)}",
                    flush=True,
                )

                last_d_stored_cur = last_daily.get(risin)
                if _rl_m_rows and not dry_run:
                    new_last = _rl_m_rows[-1]["Date"]
                    _pg = is_postgres_connection(conn)
                    _ph = "%s" if _pg else "?"
                    _nav_isin_col = "isin" if _pg else "ISIN"
                    if last_d_stored_cur:
                        conn.execute(
                            f"UPDATE nav_sources SET last_nav_date={_ph}, last_checked={_ph}, "
                            f"nav_count=(SELECT COUNT(*) FROM fund_nav_monthly WHERE {_nav_isin_col}={_ph}), "
                            f"data_status='OK' WHERE isin={_ph}",
                            (new_last, _today.isoformat(), risin, risin))
                    else:
                        conn.execute(
                            f"UPDATE nav_sources SET first_nav_date={_ph}, last_nav_date={_ph}, "
                            f"nav_count={_ph}, last_checked={_ph}, data_status='OK' WHERE isin={_ph}",
                            (_rl_m_rows[0]["Date"], new_last, len(_rl_m_rows),
                             _today.isoformat(), risin))

                ok_count += 1
                _batch_pending += 1
                if _batch_pending >= _BATCH_COMMIT and not dry_run:
                    conn.commit()
                    _batch_pending = 0

        # ----------------------------------------------------------------
        # Fase 3: reintentar ISINs con 401 — refrescar token una sola vez
        # ----------------------------------------------------------------
        if auth_retry:
            print(f"\n  [token] {len(auth_retry)} ISINs con 401 — renovando...",
                  flush=True)
            try:
                bearer = _get_bearer_token()
                bearer_acquired_at = time.time()
                print("  [token] ok.\n", flush=True)
                for ridx, risin, rms_id, rcurr, reff_desde in auth_retry:
                    print(f"  [RETRY] {risin}", end=" ", flush=True)
                    rnav_rows, rerr, _ = _download_nav_daily(
                        rms_id, risin, rcurr, reff_desde, bearer)
                    if rnav_rows:
                        time.sleep(random.uniform(*MS_DELAY_LOAD_OK))
                        rnav_rows  = _splice_new_chart_batch(conn, risin, rnav_rows)
                        _rl_d_wr   = _write_nav_rows_daily(conn, rnav_rows, dry_run)
                        _rl_m_rows = _resample_to_monthly(rnav_rows)
                        _rl_m_wr   = _write_nav_rows(conn, _rl_m_rows, dry_run)
                        total_written += _rl_m_wr
                        print(f"-> {_rl_d_wr}d/{_rl_m_wr}m", flush=True)
                        ok_count += 1
                        _batch_pending += 1
                        if _batch_pending >= _BATCH_COMMIT and not dry_run:
                            conn.commit()
                            _batch_pending = 0
                    else:
                        print(f"-> sin datos ({rerr})", flush=True)
                        errors_load += 1
            except RuntimeError as e:
                print(f"  [token] ERROR renovando: {e}", flush=True)
                print(f"[ERROR] Token chartservice no renovable en mitad del proceso. "
                      f"{len(auth_retry)} ISINs sin reintentar.", file=sys.stderr, flush=True)
                for _, risin, *_ in auth_retry:
                    errors_load += 1

        if _batch_pending > 0 and not dry_run:
            conn.commit()

        print(f"\n{'-'*50}")
        print(f"  Total NAV escritos : {total_written}")
        print(f"  Al dia (sin fetch) : {al_dia_count}")
        print(f"  Fondos sin datos   : {errors_load}")
        if dry_run:
            print("  (DRY-RUN: nada escrito en fund_nav_daily / fund_nav_monthly)")
        # Routing de severidad → stderr (monitoreado como indicador de salud del proceso)
        if errors_load > 0 and not dry_run:
            print(
                f"[WARN] run_load: {errors_load}/{total} fondos sin datos NAV "
                f"(no_access/empty/transient). Ver stdout log para detalle.",
                file=sys.stderr, flush=True,
            )
        return

    # ================================================================
    # Modo secuencial (workers=1, default) — loop original
    # ================================================================
    for idx, (isin, ms_id) in enumerate(rows, 1):
        _t0 = datetime.now()

        ds = data_status_map.get(isin, "OK") or "OK"

        # -- STALE_FROZEN: fuente estructuralmente detenida, nunca se procesa --
        if ds == "STALE_FROZEN":
            ok_count += 1
            print(f"  [{idx:>4}/{total}] {isin} -> STALE_FROZEN, omitido", flush=True)
            continue

        # -- RECALCULATE_MONTHLY: sin red, solo resamplear diario→mensual ----
        if ds == "RECALCULATE_MONTHLY":
            _daily = conn.execute(
                "SELECT ISIN, Date, NAV, NAV_Currency, NAV_Type, "
                "Is_Estimated, Data_Source FROM fund_nav_daily "
                "WHERE ISIN=? AND Data_Source='MORNINGSTAR_CHART' ORDER BY Date",
                (isin,)
            ).fetchall()
            if not _daily:
                print("-> sin datos diarios para recalcular", flush=True)
                continue
            _monthly = _resample_to_monthly([dict(r) for r in _daily])
            _written = _overwrite_nav_rows_monthly(conn, isin, _monthly, dry_run)
            if not dry_run:
                _ph = "%s" if is_postgres_connection(conn) else "?"
                conn.execute(
                    f"UPDATE nav_sources SET data_status='OK', last_nav_date={_ph}, "
                    f"nav_count={_ph} WHERE isin={_ph}",
                    (_monthly[-1]["Date"] if _monthly else None, len(_monthly), isin)
                )
                _batch_pending += 1
                if _batch_pending >= _BATCH_COMMIT:
                    conn.commit(); _batch_pending = 0
            _elapsed = (datetime.now() - _t0).total_seconds()
            print(f"-> {_written}m recalculados (sin red)  [{_elapsed:.1f}s]", flush=True)
            ok_count += 1
            total_written += _written
            continue

        # Si no hay code en nav_sources, intentar resolverlo ahora
        if not ms_id:
            resolved = _resolve_isin(isin)
            if resolved and resolved.get("code"):
                ms_id = resolved["code"]
                _ph = "%s" if is_postgres_connection(conn) else "?"
                conn.execute(
                    f"UPDATE nav_sources SET source_id={_ph} WHERE isin={_ph}",
                    (ms_id, isin)
                )
                conn.commit()

        if not ms_id:
            print("-> ERROR: no se pudo obtener code Morningstar")
            errors_load += 1
            continue

        currency = currency_map.get(isin, "EUR")

        # -- Delta: calcular ventana de descarga minima ----------------------
        last_d_stored = last_daily.get(isin)
        force_this    = force or (ds == "FORCE_REFRESH")
        if last_d_stored and not force_this:
            anchor = datetime.strptime(last_d_stored, "%Y-%m-%d").date()
            if anchor >= _cutoff:
                _elapsed_ms = round((datetime.now() - _t0).total_seconds() * 1000)
                _ts_skip    = _t0.strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[{idx:4d}/{total:4d}] | {_ts_skip} | {isin} | "
                    f"0d/0m | [{anchor} -> {anchor}] | {_elapsed_ms} | Al dia",
                    flush=True,
                )
                al_dia_count += 1
                continue
            # Solapamiento de 3 dias para capturar correcciones tardias
            eff_desde = (anchor - timedelta(days=3)).isoformat()
        else:
            eff_desde = desde   # carga completa desde el inicio

        # -- Descarga diaria via chartservice --------------------------------
        nav_rows, err_type, _ = _download_nav_daily(
            ms_id, isin, currency, eff_desde, bearer)

        if err_type == "auth":
            token_age = time.time() - bearer_acquired_at
            if token_age > _TOKEN_MAX_AGE_S:
                # Token probablemente caducado — refrescar y reintentar una vez
                print("\n  [token] renovando (>45 min)...", flush=True)
                try:
                    bearer = _get_bearer_token()
                    bearer_acquired_at = time.time()
                    print("  [token] ok.", flush=True)
                except RuntimeError:
                    print("  [token] ERROR: no se pudo renovar.", flush=True)
                nav_rows, err_type, _ = _download_nav_daily(
                    ms_id, isin, currency, eff_desde, bearer)
            else:
                # Token reciente -> security no disponible en chartservice
                err_type = "no_access"

        if not nav_rows:
            if err_type == "empty":
                print("-> sin datos (fondo sin historico en chartservice)")
            elif err_type == "no_access":
                print("-> sin datos (security no accesible en chartservice)")
            else:
                print(f"-> sin datos ({err_type})")
            time.sleep(random.uniform(*MS_DELAY_LOAD_ERR))
            errors_load += 1
            continue

        time.sleep(random.uniform(*MS_DELAY_LOAD_OK))

        # -- v24: persistir diario + mensual (INSERT OR IGNORE en ambas) ----
        nav_rows        = _splice_new_chart_batch(conn, isin, nav_rows)
        daily_written   = _write_nav_rows_daily(conn, nav_rows, dry_run)
        monthly_rows    = _resample_to_monthly(nav_rows)
        monthly_written = _write_nav_rows(conn, monthly_rows, dry_run)
        total_written  += monthly_written
        display_m   = len(monthly_rows) if dry_run else monthly_written
        display_d   = len(nav_rows)     if dry_run else daily_written
        _elapsed    = (datetime.now() - _t0).total_seconds()
        _elapsed_ms = round(_elapsed * 1000)
        _ts_load    = _t0.strftime("%Y-%m-%d %H:%M:%S")
        _date_range = f"{nav_rows[0]['Date']} -> {nav_rows[-1]['Date']}"
        print(
            f"[{idx:4d}/{total:4d}] | {_ts_load} | {isin} | "
            f"{display_d}d/{display_m}m | [{_date_range}] | {_elapsed_ms}"
        )

        # -- Actualizar nav_sources con rango real descargado --------------
        if monthly_rows and not dry_run:
            new_last = monthly_rows[-1]["Date"]
            today_s  = _today.isoformat()
            _pg = is_postgres_connection(conn)
            _ph = "%s" if _pg else "?"
            _nav_isin_col = "isin" if _pg else "ISIN"
            if last_d_stored:
                # Delta: conservar first_nav_date original; nav_count desde la tabla
                conn.execute(
                    f"UPDATE nav_sources SET last_nav_date={_ph}, last_checked={_ph}, "
                    f"nav_count=(SELECT COUNT(*) FROM fund_nav_monthly WHERE {_nav_isin_col}={_ph}), "
                    f"data_status='OK' WHERE isin={_ph}",
                    (new_last, today_s, isin, isin))
            else:
                # Carga inicial: escribir rango completo
                conn.execute(
                    f"UPDATE nav_sources SET first_nav_date={_ph}, last_nav_date={_ph}, "
                    f"nav_count={_ph}, last_checked={_ph}, data_status='OK' WHERE isin={_ph}",
                    (monthly_rows[0]["Date"], new_last, len(monthly_rows), today_s, isin))

        ok_count += 1
        _batch_pending += 1
        if _batch_pending >= _BATCH_COMMIT and not dry_run:
            conn.commit()
            _batch_pending = 0
        if ok_count > 1 and ok_count % MS_COOLDOWN_EVERY == 0:
            cooldown = random.uniform(*MS_COOLDOWN_SECS)
            print(f"\n  -- Cooldown tras {ok_count} exitos: esperando {cooldown:.0f}s --\n",
                  flush=True)
            time.sleep(cooldown)

        if verbose:
            for r in nav_rows[:3]:
                print(f"      {r['Date']}  {r['NAV']:.4f} {r['NAV_Currency']}")

    # Commit final para el ultimo lote (puede ser < _BATCH_COMMIT)
    if _batch_pending > 0 and not dry_run:
        conn.commit()

    print(f"\n{'-'*50}")
    print(f"  Total NAV escritos : {total_written}")
    print(f"  Al dia (sin fetch) : {al_dia_count}")
    print(f"  Fondos sin datos   : {errors_load}")
    if dry_run:
        print("  (DRY-RUN: nada escrito en fund_nav_daily / fund_nav_monthly)")
    if errors_load > 0 and not dry_run:
        print(
            f"[WARN] run_load: {errors_load}/{total} fondos sin datos NAV "
            f"(no_access/empty/transient). Ver stdout log para detalle.",
            file=sys.stderr, flush=True,
        )


# ============================================================
# Modo UPDATE
# ============================================================

def run_update(conn, dry_run, bearer_token=None, stale_days=3, monthly_grain=False):
    rows = conn.execute(
        "SELECT isin, source_id, last_nav_date FROM nav_sources "
        "WHERE status='OK' ORDER BY isin"
    ).fetchall()

    if not rows:
        print("No hay fondos con status=OK en nav_sources.")
        return

    # -- Preload: currency map + ultimo dia diario + data_status por ISIN -----
    _today  = date.today()
    _cutoff = _effective_cutoff(_today, stale_days)
    _ref_me = _latest_published_month_end(_today)
    last_daily_upd = {r[0]: r[1] for r in conn.execute(
        "SELECT ISIN, MAX(Date) FROM fund_nav_daily "
        "WHERE Data_Source='MORNINGSTAR_CHART' GROUP BY ISIN"
    ).fetchall()}
    currency_map = {r[0]: (r[1] or "EUR") for r in conn.execute(
        "SELECT ISIN, Fund_Currency FROM fund_master"
    ).fetchall()}
    data_status_upd = {r[0]: (r[1] or "OK") for r in conn.execute(
        "SELECT isin, data_status FROM nav_sources WHERE status='OK'"
    ).fetchall()}

    # -- Pre-filtro a nivel de conjunto: itera SOLO fondos que necesitan trabajo.
    # Sin esto el loop enumera todo el universo OK y el "al dia" se resuelve por
    # fondo, dando un header enganoso ("N fondos" = universo entero). Con el
    # pre-filtro el conteo refleja el work-set real. El gate por-fondo (mas abajo)
    # se mantiene como red de seguridad con la MISMA logica (_is_stale).
    _universe = len(rows)
    rows = [
        r for r in rows
        if _is_stale(
            data_status_upd.get(r[0], "OK") or "OK",
            last_daily_upd.get(r[0]),
            r[2],                      # last_nav_date (mensual)
            _cutoff, _ref_me, monthly_grain,
        )
    ]

    total          = len(rows)
    total_written  = 0
    al_dia_count   = 0
    errors_update  = 0
    _BATCH_COMMIT  = 20
    _batch_pending = 0
    if not dry_run and not is_postgres_connection(conn):
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -65536")

    # ── Auto-freeze: detectar series estructuralmente detenidas ───────────────
    # Nota: NO se toca fund_master.In_Current_Universe — eso es dominio P1;
    # revisar manualmente los ISINs marcados y actualizar via SQL o P1 rerun.
    _to_freeze = _auto_freeze_stale_navs(conn, dry_run)
    if _to_freeze:
        # Rebuild data_status map to pick up the new flags before pre-filter
        data_status_upd = {r[0]: (r[1] or "OK") for r in conn.execute(
            "SELECT isin, data_status FROM nav_sources WHERE status='OK'"
        ).fetchall()}
        # Re-apply pre-filter to drop newly frozen funds from work set
        rows = [
            r for r in rows
            if _is_stale(
                data_status_upd.get(r[0], "OK") or "OK",
                last_daily_upd.get(r[0]),
                r[2],
                _cutoff, _ref_me, monthly_grain,
            )
        ]
        total = len(rows)

    _grain = "mensual (fin de mes)" if monthly_grain else f"diario (stale_days={stale_days})"
    print(f"Actualizacion para {total} fondos (de {_universe} OK) | "
          f"grano={_grain} | cutoff={_cutoff if not monthly_grain else _ref_me} | "
          f"dry_run={dry_run}\n")

    if not rows:
        print("  Todos los fondos estan al dia. Nada que actualizar.")
        return

    # -- Bearer token para chartservice ------------------------------------
    if bearer_token:
        bearer = bearer_token
        print("  Bearer token chartservice suministrado via --bearer-token.\n")
    else:
        try:
            bearer = _get_bearer_token()
            print("  Bearer token chartservice obtenido.\n")
        except RuntimeError as e:
            print(f"\n  ERROR: {e}")
            print(f"[ERROR] Bearer token no disponible. Pipeline update abortado.", file=sys.stderr, flush=True)
            return

    bearer_acquired_at = time.time()
    _TOKEN_MAX_AGE_S   = 45 * 60

    for idx, (isin, ms_id, last_nav_date) in enumerate(rows, 1):
        _t0 = datetime.now()

        currency = currency_map.get(isin, "EUR")
        ds = data_status_upd.get(isin, "OK") or "OK"

        # -- RECALCULATE_MONTHLY: sin red, solo resamplear diario→mensual ----
        if ds == "RECALCULATE_MONTHLY":
            _daily = conn.execute(
                "SELECT ISIN, Date, NAV, NAV_Currency, NAV_Type, "
                "Is_Estimated, Data_Source FROM fund_nav_daily "
                "WHERE ISIN=? AND Data_Source='MORNINGSTAR_CHART' ORDER BY Date",
                (isin,)
            ).fetchall()
            if not _daily:
                print("-> sin datos diarios para recalcular", flush=True)
                continue
            _monthly = _resample_to_monthly([dict(r) for r in _daily])
            _written = _overwrite_nav_rows_monthly(conn, isin, _monthly, dry_run)
            if not dry_run:
                _ph = "%s" if is_postgres_connection(conn) else "?"
                conn.execute(
                    f"UPDATE nav_sources SET data_status='OK', last_nav_date={_ph}, "
                    f"nav_count={_ph} WHERE isin={_ph}",
                    (_monthly[-1]["Date"] if _monthly else None, len(_monthly), isin)
                )
                _batch_pending += 1
                if _batch_pending >= _BATCH_COMMIT:
                    conn.commit(); _batch_pending = 0
            _elapsed = (datetime.now() - _t0).total_seconds()
            print(f"-> {_written}m recalculados (sin red)  [{_elapsed:.1f}s]", flush=True)
            continue

        # Ancla delta: preferir ultimo dia diario; fallback a nav_sources mensual
        last_d_stored = last_daily_upd.get(isin)
        force_this    = (ds == "FORCE_REFRESH")

        # Gate "al dia" (cadence-aware, MISMA logica que el pre-filtro _is_stale).
        # Red de seguridad: tras el pre-filtro casi nunca dispara, pero cubre
        # carreras y mantiene al_dia_count coherente.
        if not _is_stale(ds, last_d_stored, last_nav_date,
                         _cutoff, _ref_me, monthly_grain):
            _anchor_disp = last_d_stored or last_nav_date or ""
            _elapsed_ms  = round((datetime.now() - _t0).total_seconds() * 1000)
            _ts_skip     = _t0.strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{idx:4d}/{total:4d}] | {_ts_skip} | {isin} | "
                f"0d/0m | [{_anchor_disp} -> {_anchor_disp}] | {_elapsed_ms} | Al dia",
                flush=True,
            )
            al_dia_count += 1
            continue

        # Ventana de descarga minima. La descarga es SIEMPRE diaria via
        # chartservice; monthly_grain solo cambia la decision de skip (arriba),
        # no la profundidad de descarga.
        if last_d_stored and not force_this:
            anchor = datetime.strptime(last_d_stored, "%Y-%m-%d").date()
            desde  = (anchor - timedelta(days=3)).isoformat()
        elif force_this:
            desde = _today.replace(day=1).isoformat()
        elif last_nav_date:
            try:
                d     = datetime.strptime(last_nav_date, "%Y-%m-%d")
                month = d.month - 2
                year  = d.year
                if month <= 0:
                    month += 12
                    year  -= 1
                desde = date(year, month, 1).isoformat()
            except Exception:
                desde = _today.replace(day=1).isoformat()
        else:
            desde = _today.replace(day=1).isoformat()

        code = ms_id or isin
        nav_rows, err_type, _ = _download_nav_daily(
            code, isin, currency, desde, bearer)

        if err_type == "auth":
            token_age = time.time() - bearer_acquired_at
            if token_age > _TOKEN_MAX_AGE_S:
                try:
                    bearer = _get_bearer_token()
                    bearer_acquired_at = time.time()
                except RuntimeError:
                    pass
                nav_rows, err_type, _ = _download_nav_daily(
                    code, isin, currency, desde, bearer)
            else:
                err_type = "no_access"

        time.sleep(random.uniform(*MS_DELAY_LOAD_OK))

        if not nav_rows:
            if err_type in ("no_access", "transient"):
                print(f"-> sin datos ({err_type})")
                errors_update += 1
            else:
                print("-> sin datos nuevos")
            continue

        # -- v24: persistir diario + mensual --------------------------------
        nav_rows      = _splice_new_chart_batch(conn, isin, nav_rows)
        daily_written = _write_nav_rows_daily(conn, nav_rows, dry_run)
        monthly_rows  = _resample_to_monthly(nav_rows)
        written       = _write_nav_rows(conn, monthly_rows, dry_run)
        total_written += written
        _elapsed    = (datetime.now() - _t0).total_seconds()
        _elapsed_ms = round(_elapsed * 1000)
        _ts_upd     = _t0.strftime("%Y-%m-%d %H:%M:%S")
        _dr_upd     = f"{nav_rows[0]['Date']} -> {nav_rows[-1]['Date']}"
        print(
            f"[{idx:4d}/{total:4d}] | {_ts_upd} | {isin} | "
            f"{daily_written}d/{written}m | [{_dr_upd}] | {_elapsed_ms}"
        )

        if monthly_rows and not dry_run:
            new_last = max(r["Date"] for r in monthly_rows)
            _ph = "%s" if is_postgres_connection(conn) else "?"
            conn.execute(
                f"UPDATE nav_sources SET last_nav_date={_ph}, last_checked={_ph}, "
                f"data_status='OK' WHERE isin={_ph}",
                (new_last, _today.isoformat(), isin)
            )
        _batch_pending += 1
        if _batch_pending >= _BATCH_COMMIT and not dry_run:
            conn.commit()
            _batch_pending = 0

    if _batch_pending > 0 and not dry_run:
        conn.commit()

    print(f"\n{'-'*50}")
    print(f"  Total NAV escritos : {total_written}")
    print(f"  Al dia (sin fetch) : {al_dia_count}")
    print(f"  Fondos con error   : {errors_update}")
    if dry_run:
        print("  (DRY-RUN: nada escrito)")
    if errors_update > 0 and not dry_run:
        print(
            f"[WARN] run_update: {errors_update}/{total} fondos con error NAV "
            f"(no_access/transient). Ver stdout log para detalle.",
            file=sys.stderr, flush=True,
        )


# ============================================================
# Modo RECALCULATE-MONTHLY
# ============================================================

def run_recalculate_monthly(conn, isins=None, dry_run=False):
    """Resamplea la serie diaria existente a mensual, sin ninguna llamada de red.

    Útil cuando se ha corregido la lógica de resample o se detectaron errores en
    fund_nav_monthly. La fuente de verdad es fund_nav_daily (chartservice); este
    modo regenera fund_nav_monthly limpiamente desde ella.

    Opera sobre:
      - La lista explícita `isins` si se pasa, O
      - Todos los ISINs con data_status='RECALCULATE_MONTHLY' en nav_sources.

    Tras el recálculo, pone data_status='OK'.
    """
    if isins:
        ph   = ",".join("?" * len(isins))
        rows = conn.execute(
            f"SELECT isin FROM nav_sources WHERE isin IN ({ph})", isins
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT isin FROM nav_sources "
            "WHERE data_status='RECALCULATE_MONTHLY' AND status='OK'"
        ).fetchall()

    if not rows:
        print("No hay ISINs con data_status='RECALCULATE_MONTHLY' en nav_sources.")
        print("Usa:  UPDATE nav_sources SET data_status='RECALCULATE_MONTHLY' WHERE isin='X';")
        return

    total  = len(rows)
    done   = 0
    errors = 0
    total_written = 0
    _BATCH_COMMIT  = 50
    _batch_pending = 0
    if not dry_run and not is_postgres_connection(conn):
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -65536")

    print(f"Recalculando {total} ISINs (diario→mensual, sin red) | dry_run={dry_run}\n")

    for idx, row in enumerate(rows, 1):
        isin = row[0]
        print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)

        daily = conn.execute(
            "SELECT ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source "
            "FROM fund_nav_daily "
            "WHERE ISIN=? AND Data_Source='MORNINGSTAR_CHART' ORDER BY Date",
            (isin,)
        ).fetchall()

        if not daily:
            print("-> sin datos diarios (omitido)", flush=True)
            errors += 1
            continue

        daily_dicts = [dict(r) for r in daily]
        monthly     = _resample_to_monthly(daily_dicts)
        written     = _overwrite_nav_rows_monthly(conn, isin, monthly, dry_run)
        total_written += written

        if not dry_run:
            _ph = "%s" if is_postgres_connection(conn) else "?"
            conn.execute(
                f"UPDATE nav_sources SET data_status='OK', last_nav_date={_ph}, "
                f"nav_count={_ph} WHERE isin={_ph}",
                (monthly[-1]["Date"] if monthly else None, len(monthly), isin)
            )
            _batch_pending += 1
            if _batch_pending >= _BATCH_COMMIT:
                conn.commit()
                _batch_pending = 0

        print(f"-> {written}m recalculados  ({daily_dicts[0]['Date']} → {daily_dicts[-1]['Date']})",
              flush=True)
        done += 1

    if _batch_pending > 0 and not dry_run:
        conn.commit()

    print(f"\n{'-'*50}")
    print(f"  ISINs recalculados : {done}/{total}")
    print(f"  NAV mensuales ok   : {total_written}")
    print(f"  ISINs sin daily    : {errors}")
    if dry_run:
        print("  (DRY-RUN: nada escrito en fund_nav_monthly)")
    print(f"\n  Siguiente paso: recalcular métricas P2 para estos ISINs:")
    print(f"    UPDATE nav_sources SET data_status='RECALCULATE_METRICS'")
    print(f"    WHERE data_status='OK' AND isin IN (<los ISINs>;")


# ============================================================
# Helper
# ============================================================

def _print_summary(found, not_found, errors, total, dry_run):
    print(f"\n{'-'*50}")
    print(f"  Encontrados    : {found:>4}  ({found*100//total if total else 0}%)")
    print(f"  No encontrados : {not_found:>4}  (ISIN genuinamente ausente)")
    print(f"  Saltados       : {errors:>4}  (challenge/error de red — BD sin cambios)")
    print(f"  TOTAL          : {total:>4}")
    if dry_run:
        print("  (DRY-RUN: nada escrito en nav_sources)")


# ============================================================
# Entry point
# ============================================================

class _Tee:
    """Escribe a la vez en un fichero de log y en el stdout original.

    Permite que cada invocacion genere su propio log con timestamp sin
    depender de redirecciones del shell (> archivo).
    """
    def __init__(self, filepath, original):
        self._f    = open(filepath, "w", buffering=1, encoding="utf-8")
        self._orig = original

    def write(self, s):
        self._f.write(s)
        self._orig.write(s)

    def flush(self):
        self._f.flush()
        self._orig.flush()

    def fileno(self):           # necesario para subprocess / Playwright
        return self._orig.fileno()

    def close(self):
        self._f.close()


def main():
    parser = argparse.ArgumentParser(
        description="Descubrimiento y descarga NAV via Morningstar (mstarpy 8.x)"
    )
    parser.add_argument("--mode",    required=True,
                        choices=["discover", "load", "update", "recalculate-monthly"])
    parser.add_argument("--isin",    default=None,
                        help="Procesar solo este ISIN")
    parser.add_argument("--sample",  type=int, default=None,
                        help="Procesar N ISINs aleatorios (para pruebas)")
    parser.add_argument("--desde",   default="2000-01-01",
                        help="Fecha inicio descarga YYYY-MM-DD (default: 2000-01-01)")
    parser.add_argument("--retry-errors", action="store_true",
                        help="En modo discover, reprocesa solo ISINs con status=ERROR en nav_sources")
    parser.add_argument("--retry-notfound", action="store_true",
                        help="En modo discover, reprocesa ISINs con status=NOT_FOUND (recuperacion tras fallo masivo)")
    parser.add_argument("--skip-if-recent", action="store_true",
                        help="En modo discover, omite si >=90%% ISINs ya comprobados hoy (evita re-lanzar ~2h tras corte en load)")
    parser.add_argument("--force", action="store_true",
                        help="En modo load, descarga aunque el ISIN ya tenga NAV en la BD (sobreescribe)")
    parser.add_argument("--ms-prefix", default=None,
                        help="En modo load, solo ISINs cuyo ms_id empiece por este prefijo (ej: F0GBR)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Ejecuta sin escribir nada en la DB")
    parser.add_argument("--verbose", action="store_true",
                        help="Muestra los primeros 3 NAV de cada fondo")
    parser.add_argument("--bearer-token", default=None,
                        help="Bearer JWT para chartservice (fallback si token_chart() falla). "
                             "Obtenerlo desde DevTools -> Network -> Authorization header")
    parser.add_argument("--workers", type=int, default=1,
                        help="Hilos concurrentes para descarga (default 1=secuencial). "
                             "Recomendado: --workers 4 para bulk backfill. "
                             "Escrituras en BD siempre en hilo principal.")
    parser.add_argument("--stale-days", type=int, default=3,
                        help="Ventana de tolerancia diaria (default 3) para el gate "
                             "'al dia' en modo update/load. ALINEAR con la cadencia real "
                             "del job: 3 dias con cadencia semanal/mensual re-descarga "
                             "~todo el universo cada ejecucion.")
    parser.add_argument("--monthly-grain", action="store_true",
                        help="En modo update, decide 'al dia' por grano MENSUAL: un fondo "
                             "esta al dia si su ultimo NAV ya cubre el ultimo fin de mes "
                             "completado. A mitad de mes solo descarga huecos genuinos.")
    args = parser.parse_args()

    # -- Logs con timestamp unico por invocacion -----------------------------
    _ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_dir  = Path(__file__).resolve().parent.parent.parent.parent / "logs"
    _log_dir.mkdir(exist_ok=True)
    _log_path = _log_dir / f"navLoad_{args.mode}_{_ts}.log"
    _err_path = _log_dir / f"navLoad_{args.mode}_{_ts}_err.log"
    sys.stdout = _Tee(_log_path, sys.__stdout__)
    sys.stderr = _Tee(_err_path, sys.__stderr__)
    print(f"  Log stdout : {_log_path}", flush=True)
    print(f"  Log stderr : {_err_path}", flush=True)

    conn = get_connection()

    # v25: migración idempotente — añade data_status si no existe aún
    _ensure_data_status_column(conn)

    if args.isin:
        isins = [i.strip().upper() for i in args.isin.split(",") if i.strip()]
    else:
        all_isins = [r[0] for r in conn.execute(
            "SELECT ISIN FROM fund_master ORDER BY ISIN").fetchall()]
        isins = (random.sample(all_isins, min(args.sample, len(all_isins)))
                 if args.sample else all_isins)

    if args.mode == "discover":
        if getattr(args, 'retry_errors', False):
            error_isins = [r[0] for r in conn.execute(
                "SELECT isin FROM nav_sources WHERE status='ERROR' ORDER BY isin"
            ).fetchall()]
            if not error_isins:
                print("No hay ISINs con status=ERROR en nav_sources.")
                conn.close()
                return
            print(f"Reintentando {len(error_isins)} ISINs con status=ERROR...")
            isins = error_isins
        elif getattr(args, 'retry_notfound', False):
            nf_isins = [r[0] for r in conn.execute(
                "SELECT isin FROM nav_sources WHERE status='NOT_FOUND' ORDER BY isin"
            ).fetchall()]
            if not nf_isins:
                print("No hay ISINs con status=NOT_FOUND en nav_sources.")
                conn.close()
                return
            print(f"Reintentando {len(nf_isins)} ISINs con status=NOT_FOUND...")
            isins = nf_isins
        run_discover(conn, isins, dry_run=args.dry_run, verbose=args.verbose,
                     skip_if_recent=getattr(args, 'skip_if_recent', False))
    elif args.mode == "load":
        ms_prefix = args.ms_prefix.upper() if args.ms_prefix else None
        if ms_prefix and not args.isin:
            prefix_isins = [r[0] for r in conn.execute(
                "SELECT isin FROM nav_sources WHERE status='OK' AND source_id LIKE ? ORDER BY isin",
                (ms_prefix + "%",)
            ).fetchall()]
            if not prefix_isins:
                print(f"No hay ISINs con ms_id que empiece por '{ms_prefix}'.")
                conn.close()
                return
            print(f"Filtro --ms-prefix {ms_prefix}: {len(prefix_isins)} ISINs")
            isins_load = prefix_isins
        else:
            isins_load = isins if args.isin else None
        run_load(conn,
                 isins         = isins_load,
                 desde         = args.desde,
                 dry_run       = args.dry_run,
                 verbose       = args.verbose,
                 force         = args.force,
                 bearer_token  = args.bearer_token,
                 workers       = args.workers,
                 stale_days    = args.stale_days)
    elif args.mode == "update":
        run_update(conn, dry_run=args.dry_run, bearer_token=args.bearer_token,
                   stale_days=args.stale_days, monthly_grain=args.monthly_grain)
    elif args.mode == "recalculate-monthly":
        isins_rcm = [args.isin.strip().upper()] if args.isin else None
        run_recalculate_monthly(conn, isins=isins_rcm, dry_run=args.dry_run)

    conn.close()


if __name__ == "__main__":
    main()
