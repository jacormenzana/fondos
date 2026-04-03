# proyecto2/src/loaders/nav_discovery.py
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
    python -m proyecto2.src.loaders.nav_discovery --mode discover --isin LU1873127366 --dry-run

    # Validar con 5 ISINs aleatorios
    python -m proyecto2.src.loaders.nav_discovery --mode discover --sample 5 --dry-run

    # Descubrimiento completo (~30-90 min)
    python -m proyecto2.src.loaders.nav_discovery --mode discover

    # Descarga historica desde 2000 (una sola vez)
    python -m proyecto2.src.loaders.nav_discovery --mode load --desde 2000-01-01

    # Actualizacion mensual
    python -m proyecto2.src.loaders.nav_discovery --mode update
"""

import argparse
import sqlite3
import sys
import time
import random
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Path setup
_P2_SRC = Path(__file__).resolve().parent.parent
_ROOT   = _P2_SRC.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_P2_SRC.parent))

from shared.db import get_connection

try:
    import mstarpy
    _MSTARPY_OK = True
except ImportError:
    _MSTARPY_OK = False

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
# Acceso directo a la API de Morningstar (sin mstarpy)
# ============================================================
# Desde marzo 2026 el endpoint data-points/fields devuelve 202
# en lugar de 200, rompiendo el constructor de mstarpy.
# Las funciones directas usan:
#   - Bearer token scrapeado de una página pública (requests puro)
#   - chartservice timeseries endpoint (sin autenticación especial)
# Rendimiento: ~0.5s por fondo vs ~20s con mstarpy 9.x (Selenium)

_CHARTSERVICE_URL = "https://www.us-api.morningstar.com/QS-markets/chartservice/v2/timeseries"
_TOKEN_URL        = "https://www.morningstar.com/funds/xnas/afozx/chart"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _get_bearer_token() -> Optional[str]:
    """
    Obtiene el Bearer token scrapeando una página pública de Morningstar.
    El token es un JWT válido durante varias horas.
    No requiere autenticación ni Selenium.
    """
    try:
        r = requests.get(_TOKEN_URL, headers={"user-agent": _random_ua()}, timeout=15)
        txt = r.text
        if "token" not in txt:
            return None
        idx   = txt.find("token")
        token = txt[idx + 7 : txt.find("}", idx) - 1]
        return token if len(token) > 20 else None
    except Exception:
        return None


def _download_nav_direct(
    ms_id:    str,
    desde:    str,
    currency: str = "EUR",
    hasta:    Optional[str] = None,
) -> tuple[list[dict], str]:
    """
    Descarga NAV mensual usando chartservice directamente (sin mstarpy).

    Parametros:
        ms_id:    ID interno Morningstar (ej. 'F0GBR04EFH')
        desde:    fecha inicio YYYY-MM-DD
        currency: divisa del fondo (para el campo NAV_Currency)
        hasta:    fecha fin YYYY-MM-DD (default: hoy)

    Devuelve (rows, err_type):
        rows:     lista de dict {Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source}
        err_type: '' si OK | 'empty' si sin datos | 'transient' si error de red
    """
    token = _get_bearer_token()
    if not token:
        return [], "transient"

    headers = {
        "user-agent":    _random_ua(),
        "authorization": f"Bearer {token}",
    }
    params = {
        "query":           f"{ms_id}:nav,totalReturn",
        "frequency":       "m",
        "startDate":       desde,
        "endDate":         hasta or date.today().isoformat(),
        "trackMarketData": "3.6.3",
        "instid":          "DOTCOM",
    }
    try:
        r = requests.get(
            _CHARTSERVICE_URL,
            headers=headers,
            params=params,
            timeout=20,
        )
        if r.status_code != 200:
            return [], "transient"

        data = r.json()
        if not data or not isinstance(data, list) or "series" not in data[0]:
            return [], "empty"

        series = data[0]["series"]
        if not series:
            return [], "empty"

        rows = []
        for item in series:
            nav_val = item.get("nav") or item.get("totalReturn")
            if nav_val is None:
                continue
            rows.append({
                "ISIN":          None,          # rellenado por el caller
                "Date":          str(item["date"])[:10],
                "NAV":           float(nav_val),
                "NAV_Currency":  currency,
                "NAV_Type":      "total_return",
                "Is_Estimated":  0,
                "Data_Source":   "MORNINGSTAR",
            })

        return rows, ""

    except Exception:
        return [], "transient"





# ============================================================
# Resolucion de ISIN -> objeto Funds
# ============================================================

def _resolve_fund(isin: str) -> Optional[mstarpy.Funds]:
    """
    Intenta instanciar mstarpy.Funds con el ISIN como termino de busqueda.
    Morningstar v8 resuelve el ISIN directamente a traves del constructor.

    Devuelve el objeto Funds si se encuentra, None si no hay resultados.
    Propaga excepciones para que el llamador las registre como ERROR.
    """
    fund = mstarpy.Funds(
        term     = isin,
        language = MS_LANGUAGE,
        pageSize = 1,
    )
    # Si Morningstar no encuentra el ISIN, fund.name lanzara excepcion
    # o devolvera cadena vacia segun la version. Verificamos accediendo a name.
    name = getattr(fund, "name", None)
    if name is None:
        # Intentar acceso alternativo
        try:
            name = fund.name
        except Exception:
            return None
    return fund


def _get_ms_id(fund: mstarpy.Funds) -> str:
    """Extrae el ID interno de Morningstar del objeto Funds.
    En mstarpy 8.x el ID esta en fund.code (ej. 'F000011KV2').
    """
    return str(getattr(fund, "code", "") or "")


# ============================================================
# Descarga de rango de fechas
# ============================================================

def _get_nav_range(fund: mstarpy.Funds) -> Optional[dict]:
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

def _download_nav(fund, isin, currency, desde):
    """
    Descarga NAV mensual. Desde marzo 2026 usa _download_nav_direct
    (requests puro, sin mstarpy) para evitar el error 202 del constructor.
    El parametro fund se mantiene por compatibilidad pero se ignora.
    """
    # Obtener ms_id: preferir fund.code si disponible, sino ISIN
    ms_id = getattr(fund, "code", None) or isin
    return _download_nav_direct(ms_id, desde, currency)



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
            source_id      = excluded.source_id,
            first_nav_date = excluded.first_nav_date,
            last_nav_date  = excluded.last_nav_date,
            nav_count      = excluded.nav_count,
            last_checked   = excluded.last_checked,
            status         = excluded.status
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
    total     = len(isins)
    found     = 0
    not_found = 0
    errors    = 0

    if not _MSTARPY_OK:
        print("[ERROR] mstarpy no está instalado. Discover requiere mstarpy.")
        print("        Ejecuta: pip install mstarpy==8.0.1")
        print("        Nota: los modos load/update ya NO requieren mstarpy.")
        return

    print(f"Descubrimiento de {total} ISINs | dry_run={dry_run}\n")

    for idx, isin in enumerate(isins, 1):
        print(f"  [{idx:>4}/{total}] {isin}", end=" ", flush=True)

        try:
            fund = _resolve_fund(isin)
            time.sleep(random.uniform(*MS_DELAY_DISCOVER))
        except Exception as e:
            print(f"-> ERROR: {e}")
            _write_nav_source(conn, isin, "MORNINGSTAR", "",
                              None, None, None, "ERROR", dry_run)
            errors += 1
            continue

        if fund is None:
            print("-> NOT_FOUND")
            _write_nav_source(conn, isin, "MORNINGSTAR", "",
                              None, None, None, "NOT_FOUND", dry_run)
            not_found += 1
            continue

        ms_id = _get_ms_id(fund)
        name  = getattr(fund, "name", "") or ""

        # Solo registrar existencia -- NAV se descarga en --mode load
        print(f"-> OK  [{name[:45]}]  ms_id={ms_id or 'n/a'}")
        _write_nav_source(conn, isin, "MORNINGSTAR", ms_id,
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

        r        = conn.execute(
            "SELECT Fund_Currency FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        currency = r[0] if r and r[0] else "EUR"

        nav_rows, err_type = _download_nav_direct(
            ms_id    = ms_id or isin,
            desde    = desde,
            currency = currency,
        )

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

        nav_rows, err_type = _download_nav_direct(
            ms_id    = ms_id or isin,
            desde    = desde,
            currency = currency,
        )
        if err_type == "transient":
            time.sleep(random.uniform(*MS_DELAY_LOAD_ERR))
        else:
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
    print(f"  No encontrados : {not_found:>4}")
    print(f"  Errores        : {errors:>4}")
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
