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
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ============================================================
# Constantes API Morningstar (acceso directo, sin mstarpy constructor)
# ============================================================
# Screener: resuelve ISIN -> securityID (code interno)
_MS_SCREENER_URL  = "https://global.morningstar.com/api/v1/{lang}/tools/screener/_data"
# Performance: descarga historicalData con el code interno
_MS_PERF_URL      = "https://api-global.morningstar.com/sal-service/v1/fund/performance/v4/{code}"
_MS_APIKEY        = "lstzFDEOhfFNMLikKa0am9mgEKLBl49T"
_MS_PERF_PARAMS   = {"clientId": "MDC", "version": "4.71.0"}

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

from shared.db import get_connection

try:
    import mstarpy
except ImportError:
    print("\n[ERROR] mstarpy no esta instalado. Ejecuta: pip install mstarpy\n")
    sys.exit(1)

# ============================================================
# Configuracion
# ============================================================

MS_LANGUAGE       = "en-gb"       # idioma para la API Morningstar
MS_DELAY_DISCOVER = (0.5, 1.0)   # pausa en discover (solo existence check)
MS_DELAY_LOAD_OK  = (1.5, 3.0)   # pausa tras descarga exitosa (ser educados)
MS_DELAY_LOAD_ERR = (0.1, 0.3)   # pausa tras 401/fallo (no es rate limit, ir rapido)
MS_DELAY_RESOLVE  = (0.5, 1.5)   # pausa tras instanciar Funds (llamada al screener)
NAV_FREQUENCY     = "daily"      # mstarpy 8 solo garantiza daily; resampleamos a mensual

# Backoff exponencial para errores de red transitorios (429, timeout, DNS)
MS_RETRY_MAX      = 3            # intentos maximos por fondo
MS_BACKOFF_BASE   = 30           # segundos base (30 -> 90 -> 270)
MS_BACKOFF_FACTOR = 3            # multiplicador entre intentos

# Pausa larga periodica - solo activa si hay exitos frecuentes
MS_COOLDOWN_EVERY = 200          # cada N fondos OK (no total)
MS_COOLDOWN_SECS  = (30, 60)     # reducido - 401 no necesita cooldown


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
            return _resample_to_monthly(rows), ""

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


def _write_nav_rows(conn, rows, dry_run) -> int:
    if not rows or dry_run:
        return 0
    conn.executemany("""
        INSERT OR IGNORE INTO fund_nav_monthly
            (ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [(r["ISIN"], r["Date"], r["NAV"], r["NAV_Currency"],
           r["NAV_Type"], r["Is_Estimated"], r["Data_Source"]) for r in rows])
    conn.commit()
    return len(rows)


# ============================================================
# Modo DISCOVER
# ============================================================

def run_discover(conn, isins, dry_run, verbose):
    """
    Verifica existencia del ISIN en Morningstar y registra el code interno
    en nav_sources. Usa _resolve_isin() (general_search directo) para evitar
    mstarpy.Funds() y el endpoint /data-points/fields que devuelve 202.
    """
    total     = len(isins)
    found     = 0
    not_found = 0
    errors    = 0

    print(f"Descubrimiento de {total} ISINs | dry_run={dry_run}\n")

    for idx, isin in enumerate(isins, 1):
        print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)

        resolved = _resolve_isin(isin)
        time.sleep(random.uniform(*MS_DELAY_DISCOVER))

        # Reintentar una vez si el resultado es challenge o none
        if resolved is None or (isinstance(resolved, dict) and resolved.get("challenge")):
            resolved = _resolve_isin(isin)
            time.sleep(random.uniform(*MS_DELAY_DISCOVER))

        if isinstance(resolved, dict) and resolved.get("challenge"):
            # Endpoint bloqueado o error de red: NO escribir NOT_FOUND
            # (evita borrar codes resueltos anteriormente)
            print("-> SKIP (endpoint challenge — fila en BD sin cambios)")
            errors += 1
            continue

        if resolved is None:
            # ISIN genuinamente no existe en Morningstar
            print("-> NOT_FOUND")
            _write_nav_source(conn, isin, "MORNINGSTAR", "",
                              None, None, None, "NOT_FOUND", dry_run)
            not_found += 1
            continue

        code = resolved["code"]
        name = resolved["name"]
        print(f"-> OK  [{name[:45]}]  code={code}")
        _write_nav_source(conn, isin, "MORNINGSTAR", code,
                          None, None, None, "OK", dry_run)
        found += 1

    _print_summary(found, not_found, errors, total, dry_run)


# ============================================================
# Modo LOAD
# ============================================================

def run_load(conn, isins, desde, dry_run, verbose, force=False):
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

    # -- Checkpoint: obtener ISINs ya cargados en fund_nav_monthly ----------
    if not force:
        already = {r[0] for r in conn.execute(
            "SELECT DISTINCT isin FROM fund_nav_monthly"
        ).fetchall()}
        pendientes = [(isin, ms_id) for isin, ms_id in rows if isin not in already]
        skipped    = len(rows) - len(pendientes)
        if skipped:
            print(f"  Checkpoint: {skipped} fondos ya cargados -> se saltan.")
            print(f"             Usa --force para recargar todo.")
        rows = pendientes

    if not rows:
        print("Nada que cargar. Todos los fondos ya tienen NAV en la BD.")
        return

    total         = len(rows)
    total_written = 0
    errors_load   = 0
    ok_count      = 0
    print(f"Descargando NAV para {total} fondos | desde={desde} | dry_run={dry_run}\n")

    for idx, (isin, ms_id) in enumerate(rows, 1):
        # Cooldown periodico basado en exitos (no en total procesados)
        # se gestiona mas abajo tras contabilizar ok

        print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)

        # Si no hay code en nav_sources, intentar resolverlo ahora
        if not ms_id:
            resolved = _resolve_isin(isin)
            if resolved:
                ms_id = resolved["code"]
                conn.execute(
                    "UPDATE nav_sources SET source_id=? WHERE isin=?",
                    (ms_id, isin)
                )
                conn.commit()

        if not ms_id:
            print("-> ERROR: no se pudo obtener code Morningstar")
            errors_load += 1
            continue

        r        = conn.execute(
            "SELECT Fund_Currency FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        currency = r[0] if r and r[0] else "EUR"

        nav_rows, err_type = _download_nav(ms_id, isin, currency, desde)

        if not nav_rows:
            if err_type == "empty":
                print("-> sin datos (fondo sin historico en Morningstar)")
            else:
                print(f"-> sin datos ({err_type})")
            time.sleep(random.uniform(*MS_DELAY_LOAD_ERR))
            errors_load += 1
            continue

        time.sleep(random.uniform(*MS_DELAY_LOAD_OK))

        written = _write_nav_rows(conn, nav_rows, dry_run)
        total_written += written
        display = len(nav_rows) if dry_run else written
        print(f"-> {display} NAV  ({nav_rows[0]['Date']} -> {nav_rows[-1]['Date']})")

        # -- Actualizar nav_sources con rango real descargado --------------
        if written and not dry_run:
            first_d = nav_rows[0]["Date"]
            last_d  = nav_rows[-1]["Date"]
            conn.execute("""
                UPDATE nav_sources
                   SET first_nav_date = ?,
                       last_nav_date  = ?,
                       nav_count      = ?,
                       last_checked   = ?
                 WHERE isin = ?
            """, (first_d, last_d, written, date.today().isoformat(), isin))
            conn.commit()

        ok_count += 1
        if ok_count > 1 and ok_count % MS_COOLDOWN_EVERY == 0:
            cooldown = random.uniform(*MS_COOLDOWN_SECS)
            print(f"\n  -- Cooldown tras {ok_count} exitos: esperando {cooldown:.0f}s --\n",
                  flush=True)
            time.sleep(cooldown)

        if verbose:
            for r in nav_rows[:3]:
                print(f"      {r['Date']}  {r['NAV']:.4f} {r['NAV_Currency']}")

    print(f"\n{'-'*50}")
    print(f"  Total NAV escritos : {total_written}")
    print(f"  Fondos sin datos   : {errors_load}")
    if dry_run:
        print("  (DRY-RUN: nada escrito en fund_nav_monthly)")


# ============================================================
# Modo UPDATE
# ============================================================

def run_update(conn, dry_run):
    rows = conn.execute(
        "SELECT isin, source_id, last_nav_date FROM nav_sources "
        "WHERE status='OK' ORDER BY isin"
    ).fetchall()

    if not rows:
        print("No hay fondos con status=OK en nav_sources.")
        return

    total         = len(rows)
    total_written = 0
    print(f"Actualizacion mensual para {total} fondos | dry_run={dry_run}\n")

    for idx, (isin, ms_id, last_nav_date) in enumerate(rows, 1):
        print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)

        if last_nav_date:
            try:
                d     = datetime.strptime(last_nav_date, "%Y-%m-%d")
                month = d.month - 2
                year  = d.year
                if month <= 0:
                    month += 12
                    year  -= 1
                desde = date(year, month, 1).isoformat()
            except Exception:
                desde = date.today().replace(day=1).isoformat()
        else:
            desde = date.today().replace(day=1).isoformat()

        r        = conn.execute(
            "SELECT Fund_Currency FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        currency = r[0] if r and r[0] else "EUR"

        code = ms_id or isin
        nav_rows, _err = _download_nav(code, isin, currency, desde)
        time.sleep(random.uniform(*MS_DELAY_LOAD_OK))

        if not nav_rows:
            print("-> sin datos nuevos")
            continue

        written = _write_nav_rows(conn, nav_rows, dry_run)
        total_written += written
        print(f"-> {written} NAV nuevos")

        if written and not dry_run:
            new_last = max(r["Date"] for r in nav_rows)
            conn.execute(
                "UPDATE nav_sources SET last_nav_date=?, last_checked=? WHERE isin=?",
                (new_last, date.today().isoformat(), isin)
            )
            conn.commit()

    print(f"\n{'-'*50}")
    print(f"  Total NAV escritos: {total_written}")
    if dry_run:
        print("  (DRY-RUN: nada escrito)")


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

def main():
    parser = argparse.ArgumentParser(
        description="Descubrimiento y descarga NAV via Morningstar (mstarpy 8.x)"
    )
    parser.add_argument("--mode",    required=True,
                        choices=["discover", "load", "update"])
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
    parser.add_argument("--force", action="store_true",
                        help="En modo load, descarga aunque el ISIN ya tenga NAV en la BD (sobreescribe)")
    parser.add_argument("--ms-prefix", default=None,
                        help="En modo load, solo ISINs cuyo ms_id empiece por este prefijo (ej: F0GBR)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Ejecuta sin escribir nada en la DB")
    parser.add_argument("--verbose", action="store_true",
                        help="Muestra los primeros 3 NAV de cada fondo")
    args = parser.parse_args()

    conn = get_connection()

    if args.isin:
        isins = [args.isin.strip().upper()]
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
        run_discover(conn, isins, dry_run=args.dry_run, verbose=args.verbose)
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
                 isins   = isins_load,
                 desde   = args.desde,
                 dry_run = args.dry_run,
                 verbose = args.verbose,
                 force   = args.force)
    elif args.mode == "update":
        run_update(conn, dry_run=args.dry_run)

    conn.close()


if __name__ == "__main__":
    main()
