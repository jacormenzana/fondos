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
        → MsPerformancePayload (pydantic: forma) → _classify_index_name (semantica)
        → benchmark_normalizer
        → fund_benchmarks (source=MORNINGSTAR)

Cache negativa (Postgres, 2026-09-26): los ISINs para los que Morningstar no devuelve benchmark
se registran en control.benchmark_ms_checks y `--mode update` no los vuelve a consultar hasta
`next_check_at` (backoff 7/30/90 dias, shared/config.py). Nunca se guarda un centinela en
fund_benchmarks: pipeline.py prefiere cualquier fila MORNINGSTAR sobre KIID.
"""

import argparse
import sqlite3
import sys
import time
import random
import requests
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

_ROOT = Path(__file__).resolve().parents[3]   # c:/desarrollo/fondos
_P1   = _ROOT / "proyecto1"                     # c:/desarrollo/fondos/proyecto1

sys.path.insert(0, str(_ROOT))   # para shared.*
sys.path.insert(0, str(_P1))     # para core.*

from shared.config import (
    DB_PATH,
    BENCH_NEGATIVE_BACKOFF_DAYS,
    BENCH_ANOMALY_MIN_FUNDS,
    BENCH_ANOMALY_MIN_SHARE,
    BENCH_SCHEMA_ERROR_LIMIT,
    BENCH_CONSECUTIVE_ERROR_LIMIT,
    BENCH_MAX_ERROR_RATE,
    BENCH_EXIT_NETWORK,
)
from shared.db import get_connection, is_postgres_connection, execute_fail_soft
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
# Validacion de la respuesta de Morningstar
# ============================================================
# Dos capas, deliberadamente separadas:
#   1. FORMA (pydantic): el cuerpo es un objeto y `indexName` es un string o null. Un cambio de
#      forma del endpoint es un SCHEMA ERROR — nunca se cachea como "sin benchmark".
#   2. SEMANTICA (funcion pura): pydantic valida tipos, no sabe que strings son basura. La lista
#      de placeholders y las comprobaciones estructurales viven en _classify_index_name.

class MsPerformancePayload(BaseModel):
    """Subconjunto de performance/v4 que usa el loader. Campos extra ignorados."""
    model_config = ConfigDict(extra="ignore")

    indexName:    Optional[StrictStr] = None   # StrictStr: un int/dict/list NO se coacciona a str
    categoryName: Optional[Any]       = None   # solo informativo (raw_text); nunca falla la carga


# Placeholders del proveedor que NO son un benchmark (comparacion en minusculas).
_PLACEHOLDER_INDEX_NAMES = frozenset({
    "none", "null", "n/a", "na", "-", "--", "tbd", "unclassified", "not benchmarked",
})


def _classify_index_name(raw: Optional[str]) -> tuple[Optional[str], str]:
    """Clasifica un `indexName` ya validado en forma. Devuelve (nombre_limpio, tipo):
        'OK'          — nombre utilizable (devuelto sin espacios laterales)
        'NONE'        — ausente, null o en blanco: Morningstar no asigna benchmark
        'PLACEHOLDER' — presente pero no es un benchmark (denylist o estructura invalida:
                        < 4 caracteres, sin ninguna letra, solo puntuacion)
    """
    if raw is None:
        return None, "NONE"
    s = raw.strip()
    if not s:
        return None, "NONE"
    if s.lower() in _PLACEHOLDER_INDEX_NAMES:
        return None, "PLACEHOLDER"
    if len(s) < 4 or not any(ch.isalpha() for ch in s):
        return None, "PLACEHOLDER"
    return s, "OK"


# ============================================================
# Extraccion de benchmark
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

    Devuelve dict con claves:
        benchmark_name — nombre limpio, o None
        raw_text       — categoryName (referencia)
        error          — fallo de transporte / HTTP (reintentable; nunca se cachea)
        kind           — 'OK' | 'NONE' | 'PLACEHOLDER' | 'SCHEMA' | 'ERROR'
        rejected_raw   — valor bruto rechazado como placeholder (kind='PLACEHOLDER')
        schema_error   — descripcion del fallo de forma (kind='SCHEMA')
    """
    result = {
        "benchmark_name": None, "raw_text": None, "error": None,
        "kind": "NONE", "rejected_raw": None, "schema_error": None,
    }

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
            result["kind"] = "ERROR"
            return result

        try:
            data = r.json()
        except ValueError:
            result["schema_error"] = "el cuerpo no es JSON"
            result["kind"] = "SCHEMA"
            return result
        if not isinstance(data, dict):
            result["schema_error"] = f"el cuerpo no es un objeto ({type(data).__name__})"
            result["kind"] = "SCHEMA"
            return result

        try:
            payload = MsPerformancePayload.model_validate(data)
        except ValidationError as e:
            first = e.errors()[0]
            result["schema_error"] = (
                f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
            )[:200]
            result["kind"] = "SCHEMA"
            return result

        name, kind = _classify_index_name(payload.indexName)
        result["kind"] = kind
        if kind == "OK":
            result["benchmark_name"] = name
        elif kind == "PLACEHOLDER":
            result["rejected_raw"] = payload.indexName

        # Guardar categoryName como raw_text para referencia
        if payload.categoryName:
            result["raw_text"] = str(payload.categoryName)[:200]

    except Exception as e:
        result["error"] = str(e)[:100]
        result["kind"] = "ERROR"

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

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')

    # Postgres migration Phase 5c (2026-09-20): INSERT OR REPLACE -> ON CONFLICT DO UPDATE.
    # Faithful translation, not just placeholder swap: SQLite's REPLACE is DELETE+INSERT, so any
    # column NOT in the explicit column list (here, benchmark_role) is silently reset to its
    # DEFAULT ('asset_proxy') on every write that hits an existing (ISIN, source) row — a real,
    # pre-existing behavior of this function, not something introduced by this port. A plain
    # ON CONFLICT DO UPDATE that omits benchmark_role from SET would instead PRESERVE its old
    # value, which is a behavior change from SQLite. Explicit `benchmark_role = DEFAULT` in SET
    # replicates the existing SQLite behavior exactly (see fund_benchmarks DDL — benchmark_role
    # has the same DEFAULT 'asset_proxy' on both sides).
    if is_postgres_connection(conn):
        conn.execute("""
            INSERT INTO fund_benchmarks
                (ISIN, source, benchmark_raw, benchmark_id, benchmark_name,
                 provider, asset_class, confidence, extracted_at)
            VALUES (%s, 'MORNINGSTAR', %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ISIN, source) DO UPDATE SET
                benchmark_raw  = excluded.benchmark_raw,
                benchmark_id   = excluded.benchmark_id,
                benchmark_name = excluded.benchmark_name,
                provider       = excluded.provider,
                asset_class    = excluded.asset_class,
                confidence     = excluded.confidence,
                extracted_at   = excluded.extracted_at,
                benchmark_role = DEFAULT
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
    else:
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
# Cache negativa (control.benchmark_ms_checks) — solo Postgres
# ============================================================

def _cache_available(conn) -> bool:
    """True si el backend es Postgres y control.benchmark_ms_checks existe y es legible.
    Devuelve False (sin excepcion) en SQLite o si el DDL aun no se aplico: el loader sigue
    funcionando, simplemente sin cache."""
    if not is_postgres_connection(conn):
        return False
    ok = execute_fail_soft(conn, "SELECT 1 FROM benchmark_ms_checks LIMIT 0")
    conn.commit()
    return ok


def _negative_next_check(n_misses: int, now: datetime) -> datetime:
    """next_check_at tras el n-esimo fallo consecutivo (backoff en shared/config.py)."""
    days = BENCH_NEGATIVE_BACKOFF_DAYS[min(n_misses, len(BENCH_NEGATIVE_BACKOFF_DAYS)) - 1]
    return now + timedelta(days=days)


def _record_negative(conn, isin: str, now: datetime) -> None:
    """Registra (o incrementa) el fallo de un ISIN. Best-effort: nunca interrumpe la carga."""
    row = conn.execute(
        "SELECT n_misses FROM benchmark_ms_checks WHERE isin = %s", (isin,)
    ).fetchone()
    n = (row[0] if row else 0) + 1
    execute_fail_soft(conn, """
        INSERT INTO benchmark_ms_checks (isin, n_misses, last_checked_at, next_check_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (isin) DO UPDATE SET
            n_misses        = excluded.n_misses,
            last_checked_at = excluded.last_checked_at,
            next_check_at   = excluded.next_check_at
    """, (isin, n, now, _negative_next_check(n, now)))
    conn.commit()


def _clear_negative(conn, isin: str) -> None:
    """Borra el fallo registrado de un ISIN cuando Morningstar ya devuelve benchmark."""
    execute_fail_soft(conn, "DELETE FROM benchmark_ms_checks WHERE isin = %s", (isin,))
    conn.commit()


def _get_probe_isins(conn) -> list:
    """Hasta 2 ISINs que YA tienen benchmark Morningstar normalizado: sirven para comprobar que
    el endpoint sigue devolviendo `indexName` antes de cachear ningun fallo."""
    rows = conn.execute("""
        SELECT ns.isin, ns.source_id
        FROM nav_sources ns
        JOIN fund_benchmarks fb ON fb.isin = ns.isin AND fb.source = 'MORNINGSTAR'
        WHERE ns.status = 'OK' AND fb.benchmark_id IS NOT NULL
        ORDER BY ns.isin
        LIMIT 2
    """).fetchall()
    return [(r[0], r[1]) for r in rows]


def _probe_endpoint(conn, fetch: Optional[Callable[[str], dict]] = None) -> str:
    """'skipped' (menos de 2 ISINs con benchmark: BD nueva/DR), 'ok' (al menos uno sigue
    devolviendo un indexName valido) o 'failed' (ninguno lo devuelve: el endpoint cambio)."""
    fetch = fetch or _fetch_benchmark_direct
    probes = _get_probe_isins(conn)
    if len(probes) < 2:
        return "skipped"
    for isin, ms_id in probes:
        if fetch(ms_id or isin)["kind"] == "OK":
            return "ok"
    return "failed"


def _load_known_raw_names(conn) -> set:
    """Nombres brutos de indexName que YA estan mapeados a un benchmark normalizado: un nombre
    conocido nunca activa el circuit-breaker de anomalias (alta legitima masiva)."""
    rows = conn.execute("""
        SELECT DISTINCT benchmark_raw
        FROM fund_benchmarks
        WHERE source = 'MORNINGSTAR' AND benchmark_id IS NOT NULL AND benchmark_raw IS NOT NULL
    """).fetchall()
    return {r[0] for r in rows}


def _log_bench_event(conn, isin: Optional[str], status: str, message: str, dry_run: bool) -> None:
    """Una fila en ingestion_log (step='BENCH_MS') para que placeholders, errores de forma y
    anomalias sean consultables a lo largo del tiempo. No-op en dry-run."""
    if dry_run:
        return
    from core.sqlite_writer import log_ingestion   # import diferido: modulo pesado
    log_ingestion(conn, isin, "BENCH_MS", status, (message or "")[:500])
    conn.commit()


# ============================================================
# Funcion principal de carga
# ============================================================

def run_benchmark_load(
    conn:         sqlite3.Connection,
    isins:        list[tuple[str, Optional[str]]],  # [(isin, ms_id), ...]
    dry_run:      bool = False,
    verbose:      bool = True,
    debug:        bool = False,
    use_cache:    Optional[bool] = None,
    fetch:        Optional[Callable[[str], dict]] = None,
    throttle:     bool = True,
) -> dict:
    """
    Ejecuta la extraccion y persistencia de benchmarks para una lista de ISINs.

    Parametros:
        conn:     conexion a la BD
        isins:    lista de (isin, ms_id) — ms_id puede ser None
        dry_run:  si True, no escribe en BD
        verbose:  si True, imprime progreso fondo a fondo
        use_cache: None = automatico (Postgres con control.benchmark_ms_checks disponible y sin
                   dry-run); False la desactiva. `fetch`/`throttle` existen para tests.

    Devuelve dict con contadores: ok, no_benchmark, error, total, placeholder, schema_error,
    quarantined, negatives_recorded, probe.
    """
    fetch = fetch or _fetch_benchmark_direct
    counters = {
        'ok': 0, 'no_benchmark': 0, 'error': 0, 'total': len(isins),
        'placeholder': 0, 'schema_error': 0, 'quarantined': 0,
        'negatives_recorded': 0, 'probe': 'n/a',
        'attempted': 0, 'aborted': False,
    }
    consecutive_errors = 0

    if use_cache is None:
        use_cache = (not dry_run) and _cache_available(conn)
    suppress_negatives = False
    if use_cache:
        counters['probe'] = _probe_endpoint(conn, fetch)
        if counters['probe'] == 'failed':
            suppress_negatives = True
            print("  [WARN] [BENCH-PROBE] ningun ISIN con benchmark conocido devolvio indexName: "
                  "el endpoint pudo cambiar. No se registraran fallos en la cache en esta carga.")
        elif counters['probe'] == 'skipped' and verbose:
            print("  [INFO] [BENCH-PROBE] omitida (menos de 2 ISINs con benchmark Morningstar)")

    known_names = _load_known_raw_names(conn)
    name_counts: Counter = Counter()
    written_by_name: dict = defaultdict(list)
    quarantined: set = set()

    for idx, (isin, ms_id) in enumerate(isins, 1):
        if verbose:
            print(f"  [{idx:4d}/{len(isins)}] {isin} (ms_id={ms_id or '-'})", end=' ', flush=True)

        # Cooldown periodico
        if idx > 1 and counters['ok'] > 0 and counters['ok'] % MS_COOLDOWN_EVERY == 0:
            secs = random.uniform(*MS_COOLDOWN_SECS)
            if verbose:
                print(f"\n  [COOLDOWN] {secs:.0f}s tras {counters['ok']} fondos OK")
            if throttle:
                time.sleep(secs)

        # Llamada directa a la API — sin mstarpy, sin Selenium
        effective_ms_id = ms_id or isin
        bench_data = fetch(effective_ms_id)
        counters['attempted'] += 1

        if bench_data["error"]:
            counters['error'] += 1
            consecutive_errors += 1
            if verbose:
                print(f"ERROR ({bench_data['error']})")
            if consecutive_errors >= BENCH_CONSECUTIVE_ERROR_LIMIT:
                # An outage, not an isolated failure: further calls only burn time (each one waits
                # out its own retries). Nothing has been cached for these ISINs.
                counters['aborted'] = True
                print(f"\n  [ERROR] [BENCH-NETWORK] {consecutive_errors} consultas consecutivas "
                      f"fallidas: se detiene la carga ({counters['attempted']} de {counters['total']} "
                      f"intentadas). Ultimo error: {bench_data['error']}")
                break
            if throttle:
                time.sleep(random.uniform(*MS_DELAY_ERR))
            continue
        consecutive_errors = 0

        # Error de FORMA: el endpoint cambio de contrato. Nunca se cachea; si se repite, deja
        # de registrar fallos en esta carga.
        if bench_data["kind"] == "SCHEMA":
            counters['schema_error'] += 1
            _log_bench_event(conn, isin, "SCHEMA", bench_data["schema_error"], dry_run)
            if verbose:
                print(f"SCHEMA_ERROR ({bench_data['schema_error']})")
            if counters['schema_error'] > BENCH_SCHEMA_ERROR_LIMIT and not suppress_negatives:
                suppress_negatives = True
                print(f"  [WARN] [BENCH-SCHEMA] mas de {BENCH_SCHEMA_ERROR_LIMIT} respuestas con "
                      f"forma inesperada: no se registraran fallos en la cache en esta carga.")
            if throttle:
                time.sleep(random.uniform(*MS_DELAY_ERR))
            continue

        # Modo diagnostico
        if debug:
            print(f"\n    [DEBUG] benchmark_name={bench_data['benchmark_name']!r}")
            print(f"    [DEBUG] raw_text={str(bench_data['raw_text'])[:80]!r}")

        if bench_data["kind"] == "PLACEHOLDER":
            counters['placeholder'] += 1
            _log_bench_event(conn, isin, "PLACEHOLDER", str(bench_data["rejected_raw"]), dry_run)

        raw = bench_data['benchmark_name']

        # Circuit-breaker de anomalias: un nombre NUEVO (no mapeado antes) que aparece en muchos
        # fondos de golpe es casi seguro un placeholder nuevo del proveedor.
        if raw:
            if raw in quarantined:
                counters['quarantined'] += 1
                if verbose:
                    print("QUARANTINED")
                if throttle:
                    time.sleep(random.uniform(*MS_DELAY_OK))
                continue
            name_counts[raw] += 1
            if raw not in known_names:
                written_by_name[raw].append(isin)
                if (name_counts[raw] > BENCH_ANOMALY_MIN_FUNDS
                        and name_counts[raw] / idx > BENCH_ANOMALY_MIN_SHARE):
                    quarantined.add(raw)
                    counters['quarantined'] += 1
                    pct = 100.0 * name_counts[raw] / idx
                    msg = (f"'{raw}' en {name_counts[raw]} fondos ({pct:.0f}% de los "
                           f"procesados) y no es un benchmark conocido: en cuarentena. "
                           f"Ya escritos (revisar): {', '.join(written_by_name[raw][:25])}")
                    print(f"\n  [WARN] [BENCH-ANOMALY] {msg}")
                    _log_bench_event(conn, None, "ANOMALY", msg, dry_run)
                    if verbose:
                        print("QUARANTINED")
                    continue

        # Persistir
        status = _write_benchmark(
            conn,
            isin,
            raw,
            None,
            dry_run,
        )

        if 'NORMALIZADO' in status or 'RAW_ONLY' in status:
            counters['ok'] += 1
            if use_cache:
                _clear_negative(conn, isin)
        else:
            counters['no_benchmark'] += 1
            if use_cache and not suppress_negatives:
                _record_negative(conn, isin, datetime.now(timezone.utc))
                counters['negatives_recorded'] += 1

        if verbose:
            print(status)

        if throttle:
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
    recheck_negatives: bool = False,
) -> list[tuple[str, Optional[str]]]:
    """
    Selecciona los ISINs a procesar desde nav_sources.

    only_missing: solo ISINs sin entrada en fund_benchmarks (source=MORNINGSTAR); en Postgres
                  tambien omite los que estan en la cache negativa hasta su next_check_at
    sample:       limitar a N ISINs aleatorios
    isin_filter:  procesar solo este ISIN concreto
    recheck_negatives: ignora la cache negativa (solo tiene efecto con only_missing)
    """
    ph = "%s" if is_postgres_connection(conn) else "?"

    if isin_filter:
        rows = conn.execute(f"""
            SELECT ns.isin, ns.source_id
            FROM nav_sources ns
            WHERE ns.isin = {ph} AND ns.status = 'OK'
        """, (isin_filter,)).fetchall()
        return [(r[0], r[1]) for r in rows]

    if only_missing:
        sql = """
            SELECT ns.isin, ns.source_id
            FROM nav_sources ns
            WHERE ns.status = 'OK'
              AND NOT EXISTS (
                SELECT 1 FROM fund_benchmarks fb
                WHERE fb.ISIN = ns.isin AND fb.source = 'MORNINGSTAR'
              )
        """
        params: tuple = ()
        if not recheck_negatives and _cache_available(conn):
            sql += """
              AND NOT EXISTS (
                SELECT 1 FROM benchmark_ms_checks c
                WHERE c.isin = ns.isin AND c.next_check_at > %s
              )
            """
            params = (datetime.now(timezone.utc),)
        rows = conn.execute(sql + " ORDER BY ns.isin", params).fetchall()
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

def _print_bench_telemetry(conn, days: int = 30) -> None:
    """Resumen de los eventos BENCH_MS (PLACEHOLDER / SCHEMA / ANOMALY) de los ultimos `days`
    dias con ejemplos: una subida o un nombre valido rechazado se ve en la salida normal de
    cada ejecucion, sin depender de una revision manual."""
    ph = "%s" if is_postgres_connection(conn) else "?"
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_param = since if is_postgres_connection(conn) else since.isoformat(timespec="seconds")
    try:
        counts = conn.execute(f"""
            SELECT status, COUNT(*)
            FROM ingestion_log
            WHERE step = 'BENCH_MS' AND created_at >= {ph}
            GROUP BY status
            ORDER BY status
        """, (since_param,)).fetchall()
        print(f"\n  Eventos BENCH_MS ultimos {days} dias:")
        if not counts:
            print("    (ninguno)")
        for status, n in counts:
            samples = conn.execute(f"""
                SELECT message FROM ingestion_log
                WHERE step = 'BENCH_MS' AND status = {ph} AND created_at >= {ph}
                ORDER BY id DESC LIMIT 5
            """, (status, since_param)).fetchall()
            shown = "; ".join(str(s[0])[:60] for s in samples)
            print(f"    {status:12s} {n:5d}   ej.: {shown}")
    except Exception as e:
        conn.rollback()
        print(f"  [WARN] no se pudo leer ingestion_log para el resumen BENCH_MS: {e}")


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

    # Top benchmarks por proveedor.
    # Postgres (a diferencia del bare-column de SQLite) exige que toda columna no agregada figure
    # en el GROUP BY: MIN(benchmark_name) es fiel porque benchmark_name = canonical_name depende
    # funcionalmente de benchmark_id; COUNT(DISTINCT) avisa si esa dependencia se rompiera.
    print(f"\n  Top 10 benchmarks Morningstar:")
    rows = conn.execute("""
        SELECT benchmark_id,
               MIN(benchmark_name)            AS benchmark_name,
               COUNT(DISTINCT benchmark_name) AS n_names,
               COUNT(*)                       AS n
        FROM fund_benchmarks
        WHERE source = 'MORNINGSTAR' AND benchmark_id IS NOT NULL
        GROUP BY benchmark_id
        ORDER BY n DESC, benchmark_id
        LIMIT 10
    """).fetchall()
    for r in rows:
        flag = f"  [WARN {r[2]} nombres/id]" if r[2] > 1 else ""
        print(f"    {r[3]:4d}x  {r[0]:30s}  {r[1]}{flag}")

    _print_bench_telemetry(conn)


# ============================================================
# Entry point
# ============================================================

def _network_verdict(counters: dict) -> Optional[str]:
    """None when the run is acceptable, else why it must exit BENCH_EXIT_NETWORK. Pure, so it is
    unit-tested. A run that stopped early on consecutive failures, or whose failure rate over the
    calls actually made exceeds BENCH_MAX_ERROR_RATE, must not look clean: the ISINs it could not
    evaluate keep their previous state and are retried next run (errors are never cached)."""
    if counters.get('aborted'):
        return (f"carga detenida tras {BENCH_CONSECUTIVE_ERROR_LIMIT} fallos consecutivos "
                f"({counters['error']} errores en {counters['attempted']} consultas).")
    attempted = counters.get('attempted', 0)
    if attempted >= 20 and counters['error'] > BENCH_MAX_ERROR_RATE * attempted:
        return (f"{counters['error']} de {attempted} consultas fallaron "
                f"({100.0 * counters['error'] / attempted:.0f}% > {100 * BENCH_MAX_ERROR_RATE:.0f}% permitido).")
    return None


def main() -> int:
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
    parser.add_argument(
        "--recheck-negatives",
        action="store_true",
        help="Ignorar la cache negativa: volver a consultar ISINs sin benchmark aunque no "
             "les toque todavia (solo Postgres)",
    )
    args = parser.parse_args()

    conn = get_connection()

    if args.mode == "gaps":
        run_gap_analysis(conn)
        conn.close()
        return 0

    only_missing = (args.mode == "update") or args.only_missing

    isins = _get_isins_for_load(
        conn,
        only_missing=only_missing,
        sample=args.sample,
        isin_filter=args.isin,
        recheck_negatives=args.recheck_negatives,
    )

    if not isins:
        print("Sin ISINs que procesar. "
              "Ejecuta primero nav_discovery --mode discover "
              "(o usa --recheck-negatives si todos estan en la cache negativa).")
        conn.close()
        return 0

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
    print(f"  Placeholders:   {counters['placeholder']:5d}  (rejected_placeholder: {counters['placeholder']})")
    print(f"  Forma inesp.:   {counters['schema_error']:5d}  (schema_errors: {counters['schema_error']})")
    print(f"  Cuarentena:     {counters['quarantined']:5d}")
    print(f"  Cache negativa: {counters['negatives_recorded']:5d}  (sonda: {counters['probe']})")
    # Solo lectura: se ejecuta tambien en dry-run para que este tramo se ejercite en cada
    # ensayo (el GroupingError de 2026-09-26 solo aparecia tras una carga real).
    run_gap_analysis(conn)

    conn.close()

    # Exit code (2026-09-26 rehearsal: an outage made 610 of 687 calls fail and the run still
    # exited 0). Errors are never cached, so a failed run leaves nothing behind that a re-run
    # would not simply retry.
    verdict = _network_verdict(counters)
    if verdict:
        print(f"\n  [ERROR] [BENCH-NETWORK] {verdict}")
        print("  Los errores no se cachean: al volver la red basta relanzar el paso "
              "(P1_P2_Complete.bat --from 1).")
        return BENCH_EXIT_NETWORK
    return 0


if __name__ == "__main__":
    from shared.backlog_client import install_excepthook
    install_excepthook(object_name="benchmark_loader.py")   # unhandled failure -> backlog ticket
    sys.exit(main())
