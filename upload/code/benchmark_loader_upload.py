# proyecto1/src/loaders/benchmark_loader.py
# -*- coding: utf-8 -*-
"""
Enriquecimiento de benchmarks desde Morningstar via mstarpy.

Complementa la extraccion de KIIDs (fuente KIID en fund_benchmarks)
con el benchmark que Morningstar tiene asignado a cada fondo.

Ventajas frente a la extraccion KIID:
  - Morningstar normaliza el nombre del benchmark internamente
  - Cubre fondos donde el KIID no declaro benchmark o el parser no lo capturo
  - Proporciona el BenchmarkId de Morningstar, util para cruzar con sus
    series de rentabilidad de indice

Prerequisitos:
  - pip install mstarpy
  - nav_sources poblado con source=MORNINGSTAR y status=OK
    (ejecutar nav_discovery --mode discover primero)

Modos de uso:
    cd c:/desarrollo/fondos

    # Solo ISINs que aun no tienen benchmark de Morningstar (~1 min para muestra)
    python -m proyecto1.src.loaders.benchmark_loader --mode update

    # Forzar recarga de todos los ISINs con MS data
    python -m proyecto1.src.loaders.benchmark_loader --mode load

    # Probar con muestra de 10 ISINs
    python -m proyecto1.src.loaders.benchmark_loader --mode update --sample 10 --dry-run

    # Solo ISINs con benchmark KIID=NULL para maximo impacto
    python -m proyecto1.src.loaders.benchmark_loader --mode update --only-missing

Arquitectura:
    nav_sources (ms_id) → API Morningstar performance/v4 (requests directo)
        → indexName
        → benchmark_normalizer
        → fund_benchmarks (source=MORNINGSTAR)
"""

import argparse
import sqlite3
import sys
import time
import random
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[3]   # c:/desarrollo/fondos
_P1   = _ROOT / "proyecto1"                     # c:/desarrollo/fondos/proyecto1

sys.path.insert(0, str(_ROOT))   # para shared.*
sys.path.insert(0, str(_P1))     # para core.*

from shared.config import DB_PATH
from shared.db import get_connection
from core.benchmark_normalizer import normalize_benchmark, clean_benchmark

try:
    import mstarpy as _mstarpy_unused  # ya no necesario para benchmark
except ImportError:
    pass  # mstarpy no requerido para benchmark_loader


# ============================================================
# Configuracion
# ============================================================

# Acceso directo a la API de Morningstar (sin mstarpy, sin Selenium)
# APIKEY extraido de mstarpy/utils.py — token público de la API SAL
_MS_APIKEY        = "lstzFDEOhfFNMLikKa0am9mgEKLBl49T"
_MS_PERF_URL      = "https://api-global.morningstar.com/sal-service/v1/fund/performance/v4/{ms_id}"
_MS_DEFAULT_PARAMS = {"clientId": "MDC", "version": "4.71.0"}

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# Tiempos de pausa
MS_DELAY_OK       = (0.4, 0.9)    # pausa tras llamada exitosa
MS_DELAY_ERR      = (0.2, 0.5)    # pausa tras error
MS_COOLDOWN_EVERY = 300           # pausa larga cada N fondos OK
MS_COOLDOWN_SECS  = (15, 30)      # duracion pausa larga


# ============================================================
# Extraccion de benchmark desde mstarpy
# ============================================================

def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _fetch_benchmark_direct(ms_id: str) -> dict:
    """
    Obtiene el benchmark de un fondo via API directa de Morningstar.
    Usa el endpoint performance/v4 con el APIKEY publico — sin mstarpy,
    sin Selenium, ~0.5s por fondo.

    El endpoint devuelve:
        indexName  — nombre del benchmark asignado por Morningstar
        categoryName — categoria del fondo

    Devuelve dict con claves: benchmark_name, raw_text, error
    """
    result = {"benchmark_name": None, "raw_text": None, "error": None}

    url = _MS_PERF_URL.format(ms_id=ms_id)
    headers = {
        "apikey":     _MS_APIKEY,
        "user-agent": _random_ua(),
    }
    try:
        r = requests.get(
            url,
            headers=headers,
            params=_MS_DEFAULT_PARAMS,
            timeout=15,
        )
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}"
            return result

        data = r.json()
        if not isinstance(data, dict):
            result["error"] = "respuesta no es dict"
            return result

        index_name = data.get("indexName")
        if index_name and isinstance(index_name, str) and len(index_name) > 3:
            result["benchmark_name"] = index_name

        # Guardar categoryName como raw_text para referencia
        cat = data.get("categoryName")
        if cat:
            result["raw_text"] = str(cat)[:200]

    except Exception as e:
        result["error"] = str(e)[:100]

    return result


def _extract_benchmark(fund_unused, ms_id: str) -> dict:
    """
    Wrapper de compatibilidad que llama a _fetch_benchmark_direct.
    El parametro fund_unused se mantiene por firma pero se ignora.
    """
    data = _fetch_benchmark_direct(ms_id)
    return {
        "benchmark_name":  data["benchmark_name"],
        "benchmark_ms_id": None,
        "raw_text":        data["raw_text"],
    }


# ============================================================
# Escritura en fund_benchmarks
# ============================================================

def _write_benchmark(
    conn: sqlite3.Connection,
    isin: str,
    raw_name:    Optional[str],
    ms_id_bench: Optional[str],
    dry_run:     bool,
) -> str:
    """
    Normaliza el benchmark extraido y lo persiste en fund_benchmarks.

    Devuelve: 'NORMALIZADO' | 'RAW_ONLY' | 'NO_BENCHMARK' | 'SKIP'
    """
    if not raw_name:
        return 'NO_BENCHMARK'

    # Intentar normalizar con nuestro normalizador
    norm = normalize_benchmark(raw_name)

    if dry_run:
        if norm:
            return f"NORMALIZADO → {norm.canonical_id}"
        return f"RAW_ONLY → {raw_name[:60]}"

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    conn.execute("""
        INSERT OR REPLACE INTO fund_benchmarks
            (ISIN, source, benchmark_raw, benchmark_id, benchmark_name,
             provider, asset_class, confidence, extracted_at)
        VALUES (?, 'MORNINGSTAR', ?, ?, ?, ?, ?, ?, ?)
    """, (
        isin,
        raw_name,
        norm.canonical_id   if norm else None,
        norm.canonical_name if norm else raw_name,
        norm.provider       if norm else None,
        norm.asset_class    if norm else None,
        norm.confidence     if norm else 'LOW',
        now,
    ))
    conn.commit()

    return 'NORMALIZADO' if norm else 'RAW_ONLY'


# ============================================================
# Funcion principal de carga
# ============================================================

def run_benchmark_load(
    conn:         sqlite3.Connection,
    isins:        list[tuple[str, Optional[str]]],  # [(isin, ms_id), ...]
    dry_run:      bool = False,
    verbose:      bool = True,
    debug:        bool = False,
) -> dict:
    """
    Ejecuta la extraccion y persistencia de benchmarks para una lista de ISINs.

    Parametros:
        conn:     conexion a fondos.sqlite
        isins:    lista de (isin, ms_id) — ms_id puede ser None
        dry_run:  si True, no escribe en BD
        verbose:  si True, imprime progreso fondo a fondo

    Devuelve dict con contadores: ok, no_benchmark, error, total
    """
    counters = {'ok': 0, 'no_benchmark': 0, 'error': 0, 'total': len(isins)}

    for idx, (isin, ms_id) in enumerate(isins, 1):
        if verbose:
            print(f"  [{idx:4d}/{len(isins)}] {isin} (ms_id={ms_id or '-'})", end=' ', flush=True)

        # Cooldown periodico
        if idx > 1 and counters['ok'] > 0 and counters['ok'] % MS_COOLDOWN_EVERY == 0:
            secs = random.uniform(*MS_COOLDOWN_SECS)
            if verbose:
                print(f"\n  [COOLDOWN] {secs:.0f}s tras {counters['ok']} fondos OK")
            time.sleep(secs)

        # Llamada directa a la API — sin mstarpy, sin Selenium
        effective_ms_id = ms_id or isin
        bench_data = _fetch_benchmark_direct(effective_ms_id)

        if bench_data["error"]:
            counters['error'] += 1
            if verbose:
                print(f"ERROR ({bench_data['error']})")
            time.sleep(random.uniform(*MS_DELAY_ERR))
            continue

        # Modo diagnostico
        if debug:
            print(f"\n    [DEBUG] benchmark_name={bench_data['benchmark_name']!r}")
            print(f"    [DEBUG] raw_text={str(bench_data['raw_text'])[:80]!r}")

        # Persistir
        status = _write_benchmark(
            conn,
            isin,
            bench_data['benchmark_name'],
            None,
            dry_run,
        )

        if 'NORMALIZADO' in status or 'RAW_ONLY' in status:
            counters['ok'] += 1
        else:
            counters['no_benchmark'] += 1

        if verbose:
            print(status)

        time.sleep(random.uniform(*MS_DELAY_OK))

    return counters


# ============================================================
# Seleccion de ISINs
# ============================================================

def _get_isins_for_load(
    conn:          sqlite3.Connection,
    only_missing:  bool = False,
    sample:        Optional[int] = None,
    isin_filter:   Optional[str] = None,
) -> list[tuple[str, Optional[str]]]:
    """
    Selecciona los ISINs a procesar desde nav_sources.

    only_missing: solo ISINs sin entrada en fund_benchmarks (source=MORNINGSTAR)
    sample:       limitar a N ISINs aleatorios
    isin_filter:  procesar solo este ISIN concreto
    """
    if isin_filter:
        rows = conn.execute("""
            SELECT ns.isin, ns.source_id
            FROM nav_sources ns
            WHERE ns.isin = ? AND ns.status = 'OK'
        """, (isin_filter,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    if only_missing:
        rows = conn.execute("""
            SELECT ns.isin, ns.source_id
            FROM nav_sources ns
            WHERE ns.status = 'OK'
              AND NOT EXISTS (
                SELECT 1 FROM fund_benchmarks fb
                WHERE fb.ISIN = ns.isin AND fb.source = 'MORNINGSTAR'
              )
            ORDER BY ns.isin
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT ns.isin, ns.source_id
            FROM nav_sources ns
            WHERE ns.status = 'OK'
            ORDER BY ns.isin
        """).fetchall()

    result = [(r[0], r[1]) for r in rows]

    if sample and len(result) > sample:
        result = random.sample(result, sample)

    return result


# ============================================================
# Analisis de gaps (diagnostico)
# ============================================================

def run_gap_analysis(conn: sqlite3.Connection) -> None:
    """
    Muestra el estado de cobertura de benchmarks comparando las tres fuentes:
    KIID (Benchmark_Declared en fund_master), MORNINGSTAR (fund_benchmarks)
    y la situacion de fondos sin ninguna fuente.
    """
    print("\n=== ANALISIS DE COBERTURA DE BENCHMARKS ===\n")

    total = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]

    # KIID
    kiid_detected = conn.execute("""
        SELECT COUNT(*) FROM fund_master
        WHERE Benchmark_Declared IS NOT NULL
          AND Benchmark_Declared != 'NO_BENCHMARK'
    """).fetchone()[0]
    kiid_no = conn.execute("""
        SELECT COUNT(*) FROM fund_master
        WHERE Benchmark_Declared = 'NO_BENCHMARK'
    """).fetchone()[0]

    # Morningstar
    ms_total = conn.execute("""
        SELECT COUNT(*) FROM fund_benchmarks WHERE source = 'MORNINGSTAR'
    """).fetchone()[0]
    ms_normalizado = conn.execute("""
        SELECT COUNT(*) FROM fund_benchmarks
        WHERE source = 'MORNINGSTAR' AND benchmark_id IS NOT NULL
    """).fetchone()[0]

    # Sin ninguna fuente
    sin_nada = conn.execute("""
        SELECT COUNT(*) FROM fund_master fm
        WHERE fm.Benchmark_Declared IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM fund_benchmarks fb
            WHERE fb.ISIN = fm.ISIN
          )
    """).fetchone()[0]

    # Con Morningstar pero sin KIID
    solo_ms = conn.execute("""
        SELECT COUNT(*) FROM fund_benchmarks fb
        WHERE fb.source = 'MORNINGSTAR'
          AND EXISTS (
            SELECT 1 FROM fund_master fm
            WHERE fm.ISIN = fb.ISIN
              AND fm.Benchmark_Declared IS NULL
          )
    """).fetchone()[0]

    print(f"  Universo total:              {total:5d} fondos")
    print(f"\n  Fuente KIID:")
    print(f"    Con benchmark detectado:   {kiid_detected:5d} ({kiid_detected/total*100:.1f}%)")
    print(f"    Declarado NO_BENCHMARK:    {kiid_no:5d} ({kiid_no/total*100:.1f}%)")
    print(f"    NULL (no encontrado):      {total-kiid_detected-kiid_no:5d} ({(total-kiid_detected-kiid_no)/total*100:.1f}%)")
    print(f"\n  Fuente Morningstar:")
    print(f"    Procesados:                {ms_total:5d} ({ms_total/total*100:.1f}%)")
    print(f"    Normalizados:              {ms_normalizado:5d} ({ms_normalizado/total*100:.1f}% de procesados)")
    print(f"    Nuevos (sin KIID):         {solo_ms:5d}")
    print(f"\n  Sin ninguna fuente:          {sin_nada:5d} ({sin_nada/total*100:.1f}%)")

    # Top benchmarks por proveedor
    print(f"\n  Top 10 benchmarks Morningstar:")
    rows = conn.execute("""
        SELECT benchmark_id, benchmark_name, COUNT(*) as n
        FROM fund_benchmarks
        WHERE source = 'MORNINGSTAR' AND benchmark_id IS NOT NULL
        GROUP BY benchmark_id
        ORDER BY n DESC LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"    {r[2]:4d}x  {r[0]:30s}  {r[1]}")


# ============================================================
# Entry point
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carga de benchmarks desde Morningstar → fund_benchmarks"
    )
    parser.add_argument(
        "--mode",
        choices=["load", "update", "gaps"],
        default="update",
        help=(
            "load   = forzar recarga de todos los ISINs con MS data | "
            "update = solo ISINs sin benchmark Morningstar (default) | "
            "gaps   = mostrar analisis de cobertura sin cargar"
        ),
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Solo ISINs donde Benchmark_Declared en fund_master es NULL",
    )
    parser.add_argument(
        "--isin",
        default=None,
        help="Procesar un ISIN concreto",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Limitar a N ISINs aleatorios (util para pruebas)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validar sin escribir en BD",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reducir salida por consola",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Mostrar dict completo de information() para diagnostico",
    )
    args = parser.parse_args()

    conn = get_connection()

    if args.mode == "gaps":
        run_gap_analysis(conn)
        conn.close()
        return

    only_missing = (args.mode == "update") or args.only_missing

    isins = _get_isins_for_load(
        conn,
        only_missing=only_missing,
        sample=args.sample,
        isin_filter=args.isin,
    )

    if not isins:
        print("Sin ISINs que procesar. "
              "Ejecuta primero nav_discovery --mode discover.")
        conn.close()
        return

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}"
          f"Benchmark Loader — Morningstar")
    print(f"  ISINs a procesar: {len(isins)}")
    print(f"  Modo:             {'update (solo nuevos)' if only_missing else 'load (todos)'}")
    print()

    counters = run_benchmark_load(
        conn,
        isins,
        dry_run=args.dry_run,
        verbose=not args.quiet,
        debug=getattr(args, "debug", False),
    )

    print(f"\n=== RESUMEN ===")
    print(f"  Total:          {counters['total']:5d}")
    print(f"  Con benchmark:  {counters['ok']:5d}")
    print(f"  Sin benchmark:  {counters['no_benchmark']:5d}  (Morningstar no lo tiene asignado)")
    print(f"  Errores:        {counters['error']:5d}")
    if not args.dry_run:
        run_gap_analysis(conn)

    conn.close()


if __name__ == "__main__":
    main()
