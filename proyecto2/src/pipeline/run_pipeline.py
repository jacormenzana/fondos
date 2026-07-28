# proyecto2/src/pipeline/run_pipeline.py
# -*- coding: utf-8 -*-
"""
Pipeline canonico de Proyecto 2 (v27 — fault-tolerant, timestamped, idempotent).

Proceso por fondo:
  1. Cargar serie NAV desde fund_nav_monthly
  2. Cargar IPC desde series_inflation
  3. Calcular metricas de riesgo + consistencia (nominal y real)
  4. Calcular para horizonte since_inception y ventanas de crisis
  5. Calcular horizontes rolling si hay suficientes datos
  6. Persistir en fund_metrics
  7. Registrar trazabilidad en p2_pipeline_log
  8. Actualizar fund_metric_state (hash de inputs, v27)

Uso:
    cd c:/desarrollo/fondos/proyecto2
    python -m src.pipeline.run_pipeline

    Opciones:
    --isin LU1234567890          procesar solo este/estos ISINs (coma-separado)
    --horizon since_inception    solo ese horizonte
    --metrics risk,macro         solo estas familias de metricas
    --from-date 2020-01-01       recortar NAV desde esta fecha antes de calcular
    --to-date   2024-12-31       recortar NAV hasta esta fecha antes de calcular
    --force                      ignorar cache de input-hash (recalcular siempre)
    --dry-run                    calcula pero no escribe en DB
    --resume                     legado: salta ISINs ya procesados hoy

Familias de metricas (--metrics):
    risk, macro, momentum, capture, persistence, fx, regime, rolling, short
"""

import argparse
import ctypes
import hashlib  # noqa: F401 (used via fingerprint module)
import logging
import signal
import sqlite3
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# -- Path setup -----------------------------------------------
_P2_DIR = Path(__file__).resolve().parent.parent.parent   # proyecto2/
_ROOT   = _P2_DIR.parent                                   # c:\desarrollo\fondos
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_P2_DIR))

from shared.config import RISK_FREE_RATE_ANN, METRIC_VERSION
from shared.config import CRISIS_WINDOWS, ROLLING_WINDOWS, REGION_IPC, MIN_NAV_ROWS
from shared.config import MIN_NAV_MACRO, MIN_NAV_PERSIST
from shared.config import SHORT_WINDOWS, SHORT_WINDOW_MIN_OBS, METRIC_VERSION_SHORT
from shared.config import (
    ROLLING_STATS_ENABLED, ALERT_RULES, ROLLING_TIMESERIES_METRICS
)
from shared.db import get_connection
from src.readers.db_readers import (
    load_nav, get_isins_with_nav, load_ipc, ipc_available, load_nav_daily
)
from src.calculations.short_horizon import compute_short_horizon_metrics
from src.calculations.risk_metrics import compute_risk_metrics
from src.calculations.consistency import consistency_metrics
from src.calculations.macro_sensitivity import (
    load_macro_factors, compute_macro_sensitivity
)
from src.calculations.regime_returns import (
    load_regime_history, compute_regime_returns
)
from src.calculations.m2_global_builder import build_m2_global
from src.calculations.momentum import compute_momentum
from src.calculations.capture_ratios import compute_capture_ratios
from src.calculations.persistence import compute_persistence
from src.calculations.currency_factor import compute_currency_factor
from src.utils.validators import validate_nav, validate_ipc
from src.utils.time_windows import slice_window
from src.calculations.rolling_stats import (
    compute_rolling_rows,
    compute_category_snapshot,
    compute_alerts,
    compute_timeseries_snapshots,
    cat_signals_from_snapshot,
)
from src.utils.fingerprint import compute_input_hash
from src.utils.logger import get_pipeline_logger


# ============================================================
# Graceful-abort signal handling (v27)
# ============================================================

_ABORT: bool = False          # flipped by SIGINT / SIGBREAK


def _install_signal_handlers() -> None:
    def _handler(signum, frame):
        global _ABORT
        _ABORT = True
        sys.stderr.write(
            f"\n[PIPELINE] Signal {signum} received — "
            "finishing current fund then stopping...\n"
        )
        sys.stderr.flush()

    signal.signal(signal.SIGINT, _handler)
    try:
        signal.signal(signal.SIGBREAK, _handler)   # Windows Ctrl+Break
    except AttributeError:
        pass  # non-Windows


# ============================================================
# OS sleep prevention (v27, Windows only)
# ============================================================

_ES_CONTINUOUS      = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _prevent_sleep() -> None:
    """Tell Windows not to sleep/hibernate while the pipeline runs."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
        )
    except Exception:
        pass  # non-Windows or permission denied — not fatal


def _allow_sleep() -> None:
    """Restore normal sleep behaviour."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


# ============================================================
# fund_metric_state helpers (idempotency cache, v27)
# ============================================================

def _get_stored_hash(conn: sqlite3.Connection, isin: str) -> str | None:
    """Devuelve el input_hash almacenado para (isin, METRIC_VERSION), o None."""
    row = conn.execute(
        "SELECT input_hash FROM fund_metric_state "
        "WHERE isin=? AND metric_version=?",
        (isin, METRIC_VERSION),
    ).fetchone()
    return row[0] if row else None


def _upsert_metric_state(
    conn: sqlite3.Connection,
    isin: str,
    input_hash: str,
    dry_run: bool,
) -> None:
    """Persiste o actualiza el input_hash en fund_metric_state."""
    if dry_run:
        return
    for attempt in range(3):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO fund_metric_state
                           (isin, metric_version, input_hash, calculated_at)
                       VALUES (?, ?, ?, ?)""",
                    (isin, METRIC_VERSION, input_hash, date.today().isoformat()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise


# ============================================================
# Helpers de escritura
# ============================================================

def _write_metrics(
    conn: sqlite3.Connection,
    isin: str,
    metrics: list[dict],
    horizon: str,
    dry_run: bool,
    metric_version: str | None = None,
) -> int:
    """Persiste lista de metricas en fund_metrics. Devuelve nº escritas.

    metric_version: si None usa METRIC_VERSION ('v1'). Para métricas de
    horizonte corto pasar METRIC_VERSION_SHORT ('d1') — mantiene las series
    cortas separadas de las mensuales en la clave compuesta.
    """
    if not metrics or dry_run:
        return 0

    mv    = metric_version if metric_version is not None else METRIC_VERSION
    today = date.today().isoformat()
    sql = """
        INSERT OR REPLACE INTO fund_metrics
            (isin, metric, horizon, value, real_flag,
             calculation_date, metric_version, benchmark_id, source_rows)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
    """
    rows = [
        (
            isin,
            m["metric"],
            horizon,
            m["value"] if not (isinstance(m["value"], float) and
                                m["value"] != m["value"]) else None,  # NaN -> NULL
            m["real_flag"],
            today,
            mv,
            m.get("source_rows"),
        )
        for m in metrics
    ]
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(sql, rows)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return len(rows)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0


def _write_timeseries(
    conn: sqlite3.Connection,
    rows: list[dict],
    dry_run: bool,
) -> int:
    """Escribe filas en fund_metric_timeseries con INSERT OR IGNORE (incremental).

    Solo inserta fechas que no existen aún → comportamiento append-only.
    Devuelve el nº de filas insertadas (0 si ya existían o dry_run).
    """
    if not rows or dry_run:
        return 0
    sql = """
        INSERT OR IGNORE INTO fund_metric_timeseries
            (isin, metric, window, date, value, real_flag,
             ref_type, ref_value, source_rows)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    data = [
        (
            r["isin"], r["metric"], r["window"], r["date"],
            r["value"],
            r["real_flag"],
            r.get("ref_type"),
            r.get("ref_value"),
            r.get("source_rows"),
        )
        for r in rows
    ]
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.executemany(sql, data)
                conn.execute("COMMIT")
                return cur.rowcount if cur.rowcount >= 0 else len(data)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0


def _write_metric_alerts(
    conn: sqlite3.Connection,
    alert_rows: list[dict],
    dry_run: bool,
) -> int:
    """Escribe fund_metric_alerts (estado actual — INSERT OR REPLACE).

    La tabla es current-state: se sobreescribe la alerta existente.
    Devuelve el nº de filas escritas.
    """
    if not alert_rows or dry_run:
        return 0
    sql = """
        INSERT OR REPLACE INTO fund_metric_alerts
            (isin, metric, window, level, rule_code,
             value, reference_value, ref_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    data = [
        (
            r["isin"], r["metric"], r["window"],
            r["level"], r["rule_code"],
            r.get("value"), r.get("reference_value"), r.get("ref_type"),
        )
        for r in alert_rows
    ]
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.executemany(sql, data)
                conn.execute("COMMIT")
                return len(data)
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise
    return 0


def _log(
    conn: sqlite3.Connection,
    isin: str,
    step: str,
    status: str,
    horizon: str | None,
    message: str | None,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    for attempt in range(5):
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """INSERT INTO p2_pipeline_log
                           (isin, step, status, horizon, metric_version, message)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (isin, step, status, horizon, METRIC_VERSION, message),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) and attempt < 4:
                time.sleep(2 ** attempt)
            else:
                raise


# ============================================================
# Calculo de un horizonte
# ============================================================

def _process_horizon(
    isin: str,
    nav_df: pd.DataFrame,
    ipc_df: pd.DataFrame | None,
    horizon: str,
    conn: sqlite3.Connection,
    dry_run: bool,
) -> int:
    """Calcula y persiste todas las metricas para un horizonte dado."""
    if len(nav_df) < MIN_NAV_ROWS:
        _log(conn, isin, "CALC", "SKIP", horizon,
             f"Solo {len(nav_df)} filas NAV (minimo {MIN_NAV_ROWS})", dry_run)
        return 0

    # -- Metricas de riesgo ------------------------------------
    risk_df = compute_risk_metrics(nav_df, ipc_df, RISK_FREE_RATE_ANN)
    risk_metrics = risk_df.to_dict("records")
    for m in risk_metrics:
        m["source_rows"] = len(nav_df)

    # -- Metricas de consistencia ------------------------------
    cons_metrics = []
    for metric, value, real_flag in consistency_metrics(nav_df, ipc_df):
        cons_metrics.append({
            "metric": metric,
            "value": value,
            "real_flag": real_flag,
            "source_rows": len(nav_df),
        })

    all_metrics = risk_metrics + cons_metrics
    written = _write_metrics(conn, isin, all_metrics, horizon, dry_run)
    _log(conn, isin, "CALC", "OK", horizon,
         f"{written} metricas calculadas", dry_run)
    return written


# ============================================================
# Pipeline principal
# ============================================================

# All metric families available via --metrics.
# None means "all".
_ALL_METRIC_FAMILIES = frozenset({
    "risk", "macro", "momentum", "capture",
    "persistence", "fx", "regime", "rolling", "short",
})

# Bump this string whenever the calculation logic changes to force a
# cache-miss in fund_metric_state even when NAV/IPC inputs are unchanged.
CALC_VERSION: str = "20260727"


def run(
    isins: list[str] | None = None,
    horizons_filter: list[str] | None = None,
    metrics_filter: list[str] | None = None,    # v27 — metric families
    from_date: str | None = None,               # v27 — NAV start clip
    to_date: str | None = None,                 # v27 — NAV end clip
    force: bool = False,                        # v27 — bypass hash cache
    dry_run: bool = False,
    resume: bool = False,
) -> None:
    """
    Ejecuta el pipeline P2 completo o parcial.

    Todos los parámetros son opcionales; sin ellos procesa 3219 fondos completo.
    Consulta la docstring del módulo para descripción de cada parámetro.
    """
    # ---- Setup ------------------------------------------------
    global _ABORT
    _ABORT = False
    _install_signal_handlers()
    _prevent_sleep()

    # Line-buffered stdout/stderr — output visible inmediatamente en log
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # fallback: -u flag en el .bat garantiza unbuffered

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = get_pipeline_logger(run_id)

    # Validate metric families
    if metrics_filter is not None:
        unknown = set(metrics_filter) - _ALL_METRIC_FAMILIES
        if unknown:
            logger.warning(
                f"[PIPELINE] Familias de metricas desconocidas ignoradas: {unknown}"
            )
            metrics_filter = [f for f in metrics_filter if f in _ALL_METRIC_FAMILIES]

    def _want(family: str) -> bool:
        """True si la familia debe calcularse según metrics_filter."""
        return metrics_filter is None or family in metrics_filter

    # Counters (defined before try so finally can always reference them)
    t_run_start   = time.time()
    status        = "OK"
    n_processed   = 0
    n_skipped     = 0
    n_errors      = 0
    total_written = 0
    total         = 0
    conn          = None

    try:
        logger.info(
            f"[RUN START] run_id={run_id} dry_run={dry_run} "
            f"metrics_filter={metrics_filter} from_date={from_date} "
            f"to_date={to_date} force={force}"
        )

        conn = get_connection()

        # -- Cargar IPC --------------------------------------------
        if not ipc_available(conn, REGION_IPC):
            logger.warning(
                f"[IPC] No hay datos para '{REGION_IPC}' en series_inflation. "
                "Solo metricas nominales. Carga IPC con macro_discovery antes de P2."
            )
            ipc_df = None
        else:
            ipc_df = load_ipc(conn, REGION_IPC)
            ok, err = validate_ipc(ipc_df)
            if not ok:
                logger.warning(f"[IPC] Invalido ({err}) — solo metricas nominales")
                ipc_df = None

        # -- Cargar factores macro (una vez para todos los fondos) --
        macro_df = load_macro_factors(conn)
        if macro_df.empty:
            logger.warning(
                "[MacroFactors] Sin factores macro. "
                "Ejecuta macro_discovery antes de P2."
            )
        else:
            excluded = [c for c in ["spread_ig"] if c not in macro_df.columns]
            if excluded:
                logger.info(
                    f"  [MacroFactors] Factores excluidos por cobertura "
                    f"insuficiente: {excluded}"
                )
            logger.info(
                f"Factores macro: {list(macro_df.columns)} ({len(macro_df)} meses)"
            )

            # -- Auto-build M2 Global si no existe aún ----------------------
            if "m2_global_yoy" not in macro_df.columns:
                logger.info("  [M2_Global] No detectado en factores. Construyendo...")
                rows_built = build_m2_global(conn, dry_run=dry_run)
                if rows_built > 0:
                    logger.info(
                        f"  [M2_Global] {rows_built} registros construidos. "
                        "Recargando factores..."
                    )
                    macro_df = load_macro_factors(conn)
                else:
                    logger.warning(
                        "  [M2_Global] No se pudo construir (datos insuficientes)"
                    )

        # -- Cargar historico de regimenes --
        regime_df = load_regime_history(conn)
        if regime_df.empty:
            logger.warning(
                "[RegimeReturns] Sin historico de regimenes. "
                "Ejecuta macro_discovery y m2_global_builder antes de P2."
            )
        else:
            n_reg = (
                regime_df["regime"].nunique()
                if "regime" in regime_df.columns
                else "?"
            )
            logger.info(
                f"[RegimeReturns] Historico cargado: {len(regime_df)} meses | "
                f"{n_reg} regimenes distintos"
            )

        # -- Universo de ISINs -------------------------------------
        if isins is None:
            isins = get_isins_with_nav(conn)

        if not isins:
            logger.info(
                "No hay ISINs con datos NAV en fund_nav_monthly. "
                "Pipeline finalizado."
            )
            return

        # RECALCULATE_METRICS forzados (siempre consultados)
        force_recalc_isins: set[str] = {
            r[0] for r in conn.execute(
                "SELECT isin FROM nav_sources WHERE data_status='RECALCULATE_METRICS'"
            ).fetchall()
        }

        # Legacy --resume (today-based skip)
        if resume and not dry_run:
            today_str = date.today().isoformat()
            done = {r[0] for r in conn.execute(
                "SELECT DISTINCT isin FROM fund_metrics "
                "WHERE calculation_date = ? AND metric_version = ?",
                (today_str, METRIC_VERSION)
            ).fetchall()}
            isins = [i for i in isins if i not in done or i in force_recalc_isins]
            logger.info(
                f"  [Resume] {len(done)} fondos ya procesados hoy → saltados. "
                f"{len(force_recalc_isins)} con RECALCULATE_METRICS forzados. "
                f"{len(isins)} pendientes."
            )
            if not isins:
                logger.info(
                    "Pipeline completado (todos los fondos ya procesados hoy)."
                )
                return

        total = len(isins)
        logger.info(
            f"Procesando {total} fondos | dry_run={dry_run} | "
            f"IPC={'SI' if ipc_df is not None else 'NO'}"
        )

        # ================================================================
        # Per-fund loop
        # ================================================================
        for idx, isin in enumerate(isins, 1):

            # Graceful-abort (SIGINT / SIGBREAK)
            if _ABORT:
                status = "ABORTED"
                logger.warning(
                    f"[PIPELINE] Abort solicitado — "
                    f"deteniendo tras {n_processed} fondos procesados."
                )
                break

            t_fund = time.time()
            logger.info(f"START [{idx}/{total}] {isin}")

            try:
                # ---- NAV load + validation ---------------------------
                nav_df = load_nav(conn, isin)
                if nav_df.empty:
                    logger.debug(f"  [{idx}/{total}] {isin} -> sin NAV, saltado")
                    _log(conn, isin, "NAV_LOAD", "SKIP", None, "Sin datos NAV", dry_run)
                    n_skipped += 1
                    continue

                ok, err = validate_nav(nav_df)
                if not ok:
                    logger.warning(
                        f"  [{idx}/{total}] {isin} -> NAV invalido ({err}), saltado"
                    )
                    _log(conn, isin, "NAV_LOAD", "WARN", None, err, dry_run)
                    n_skipped += 1
                    continue

                # ---- Input-hash idempotency (v27) --------------------
                # Hash computed on FULL nav_df (before any date clipping)
                # so it represents the true data state.
                current_hash = compute_input_hash(
                    nav_df, ipc_df, METRIC_VERSION, CALC_VERSION
                )
                if not force and isin not in force_recalc_isins:
                    stored_hash = _get_stored_hash(conn, isin)
                    if stored_hash == current_hash:
                        elapsed_ms = (time.time() - t_fund) * 1000
                        logger.debug(
                            f"SKIP  [{idx}/{total}] {isin} (cache hit, "
                            f"hash={current_hash[:8]}…) in {elapsed_ms:.0f}ms"
                        )
                        n_skipped += 1
                        continue

                # ---- Optional date-range clipping --------------------
                # (--from-date / --to-date: targeted recalc of a sub-period)
                if from_date:
                    nav_df = nav_df[
                        nav_df["date"] >= pd.Timestamp(from_date)
                    ].reset_index(drop=True)
                if to_date:
                    nav_df = nav_df[
                        nav_df["date"] <= pd.Timestamp(to_date)
                    ].reset_index(drop=True)
                if len(nav_df) < MIN_NAV_ROWS:
                    logger.debug(
                        f"  [{idx}/{total}] {isin} -> NAV demasiado corto "
                        "tras recorte de fechas, saltado"
                    )
                    n_skipped += 1
                    continue

                isin_written = 0

                # ---- Since inception ---------------------------------
                if _want("risk"):
                    if horizons_filter is None or "since_inception" in horizons_filter:
                        isin_written += _process_horizon(
                            isin, nav_df, ipc_df, "since_inception", conn, dry_run
                        )

                # ---- Ventanas de crisis ------------------------------
                if _want("risk"):
                    for crisis_name, (start, end) in CRISIS_WINDOWS.items():
                        if horizons_filter and crisis_name not in horizons_filter:
                            continue
                        nav_w = slice_window(nav_df, start, end)
                        ipc_w = (
                            slice_window(ipc_df, start, end)
                            if ipc_df is not None else None
                        )
                        if len(nav_w) < MIN_NAV_ROWS:
                            continue
                        isin_written += _process_horizon(
                            isin, nav_w, ipc_w, crisis_name, conn, dry_run
                        )

                # ---- Horizontes rolling ------------------------------
                # REL-3: date-based slicing (tail(N) puede excluir meses con
                # retraso puntual de publicación).
                if _want("risk"):
                    for horizon_name, months in ROLLING_WINDOWS.items():
                        if horizons_filter and horizon_name not in horizons_filter:
                            continue
                        _cutoff = nav_df["date"].max() - pd.DateOffset(months=months)
                        nav_w   = nav_df[nav_df["date"] > _cutoff].reset_index(drop=True)
                        ipc_w   = (
                            ipc_df[ipc_df["date"] > _cutoff].reset_index(drop=True)
                            if ipc_df is not None else None
                        )
                        if len(nav_w) < MIN_NAV_ROWS:
                            continue
                        isin_written += _process_horizon(
                            isin, nav_w, ipc_w, horizon_name, conn, dry_run
                        )

                # ---- Horizontes cortos diarios (v24) -----------------
                if _want("short"):
                    nav_daily = load_nav_daily(conn, isin)
                    if not nav_daily.empty:
                        for sh_name, sh_days in SHORT_WINDOWS.items():
                            if horizons_filter and sh_name not in horizons_filter:
                                continue
                            nav_sh  = nav_daily.tail(sh_days).reset_index(drop=True)
                            min_obs = SHORT_WINDOW_MIN_OBS.get(sh_name, 15)
                            if len(nav_sh) < min_obs:
                                _log(conn, isin, "CALC", "SKIP", sh_name,
                                     f"Solo {len(nav_sh)} filas NAV diario "
                                     f"(min {min_obs})", dry_run)
                                continue
                            sh_list = compute_short_horizon_metrics(nav_sh, ipc_df)
                            sh_rows = [
                                {"metric": m, "value": v, "real_flag": rf,
                                 "source_rows": len(nav_sh)}
                                for m, v, rf in sh_list
                            ]
                            isin_written += _write_metrics(
                                conn, isin, sh_rows, sh_name, dry_run,
                                metric_version=METRIC_VERSION_SHORT
                            )
                            if sh_rows:
                                _log(conn, isin, "CALC", "OK", sh_name,
                                     f"{len(sh_rows)} metricas cortas (d1)", dry_run)

                # ---- Atributos del fondo ----------------------------
                _fm = conn.execute(
                    """SELECT Fund_Nature, Fund_Currency, Hedging_Policy,
                              Asset_Currency, Geography, Development_Status
                       FROM fund_master WHERE ISIN=?""", (isin,)
                ).fetchone()
                fund_nature        = _fm[0] if _fm else None
                fund_currency      = _fm[1] if _fm else None
                hedging_policy     = _fm[2] if _fm else None
                asset_currency     = _fm[3] if _fm else None
                geography          = _fm[4] if _fm else None
                development_status = _fm[5] if _fm else None

                # ---- Familias que requieren macro data availability --
                _has_macro = not macro_df.empty and len(nav_df) >= MIN_NAV_MACRO

                if _has_macro:
                    # -- Sensibilidad macro --------------------------
                    if _want("macro"):
                        sens_list = compute_macro_sensitivity(
                            nav_df, macro_df,
                            geography=geography,
                            development_status=development_status,
                        )
                        sens_rows = [
                            {"metric": m, "value": v, "real_flag": rf,
                             "source_rows": len(nav_df)}
                            for m, v, rf in sens_list
                        ]
                        isin_written += _write_metrics(
                            conn, isin, sens_rows, "since_inception", dry_run
                        )

                    # -- Momentum ------------------------------------
                    if _want("momentum") and fund_nature:
                        mom_list = compute_momentum(isin, fund_nature, nav_df, conn)
                        mom_rows = [
                            {"metric": m, "value": v, "real_flag": rf,
                             "source_rows": len(nav_df)}
                            for m, v, rf in mom_list
                        ]
                        isin_written += _write_metrics(
                            conn, isin, mom_rows, "since_inception", dry_run
                        )

                    # -- Capture ratios ------------------------------
                    if _want("capture") and fund_nature:
                        cap_list = compute_capture_ratios(
                            isin, fund_nature, nav_df, conn
                        )
                        cap_rows = [
                            {"metric": m, "value": v, "real_flag": rf,
                             "source_rows": len(nav_df)}
                            for m, v, rf in cap_list
                        ]
                        isin_written += _write_metrics(
                            conn, isin, cap_rows, "since_inception", dry_run
                        )

                # -- Persistencia del alpha --------------------------
                if _want("persistence") and fund_nature and len(nav_df) >= MIN_NAV_PERSIST:
                    per_list = compute_persistence(isin, fund_nature, nav_df, conn)
                    per_rows = [
                        {"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in per_list
                    ]
                    isin_written += _write_metrics(
                        conn, isin, per_rows, "since_inception", dry_run
                    )

                # -- Factor divisa ----------------------------------
                if _want("fx"):
                    fx_list = compute_currency_factor(
                        isin, fund_currency, hedging_policy, nav_df, conn,
                        asset_currency=asset_currency,
                    )
                    fx_rows = [
                        {"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in fx_list
                    ]
                    isin_written += _write_metrics(
                        conn, isin, fx_rows, "since_inception", dry_run
                    )

                # -- Retornos por regimen ---------------------------
                if _want("regime") and not regime_df.empty and len(nav_df) >= 36:
                    reg_list = compute_regime_returns(nav_df, regime_df)
                    reg_rows = [
                        {"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in reg_list
                    ]
                    isin_written += _write_metrics(
                        conn, isin, reg_rows, "since_inception", dry_run
                    )

                # -- Indicadores rolling (v26 — ROLLING_STATS_ENABLED) --
                # v28: pctile_self computed here from roll_rows (already in RAM),
                # eliminating the post-loop full-table read for the self-percentile.
                if _want("rolling") and ROLLING_STATS_ENABLED:
                    roll_rows = compute_rolling_rows(
                        isin, nav_df,
                        rolling_windows=ROLLING_WINDOWS,
                        min_obs=MIN_NAV_ROWS,
                        ipc_df=ipc_df,
                        periods_per_year=12,
                    )
                    ts_written = _write_timeseries(conn, roll_rows, dry_run)
                    if ts_written:
                        _log(conn, isin, "ROLLING", "OK", "all_windows",
                             f"{ts_written} filas timeseries rolling", dry_run)

                    # pctile_self: percentil temporal del último valor vs propio
                    # historial. roll_rows ya está en RAM — no se necesita leer la DB.
                    if roll_rows:
                        fund_ts_df = pd.DataFrame(roll_rows)
                        self_snaps = compute_timeseries_snapshots(
                            fund_ts_df, category_df=None, min_self_obs=12
                        )
                        self_by: dict[str, list] = {}
                        for r in self_snaps:
                            self_by.setdefault(r["window"], []).append({
                                "metric":      r["metric"],
                                "value":       r["value"],
                                "real_flag":   r["real_flag"],
                                "source_rows": r.get("source_rows"),
                            })
                        for w_name, w_rows in self_by.items():
                            isin_written += _write_metrics(
                                conn, isin, w_rows, w_name, dry_run
                            )

                total_written += isin_written
                elapsed_ms = (time.time() - t_fund) * 1000
                logger.info(
                    f"END   [{idx}/{total}] {isin} -> {isin_written} metricas "
                    f"in {elapsed_ms:.0f}ms"
                )

                # v25: consumir flag RECALCULATE_METRICS
                if isin_written > 0 and not dry_run:
                    conn.execute(
                        "UPDATE nav_sources SET data_status='OK' "
                        "WHERE isin=? AND data_status='RECALCULATE_METRICS'",
                        (isin,)
                    )

                # v27: upsert input-hash para skip en proximas ejecuciones
                if isin_written > 0:
                    _upsert_metric_state(conn, isin, current_hash, dry_run)

                n_processed += 1

            except Exception as exc:
                n_errors += 1
                elapsed_ms = (time.time() - t_fund) * 1000
                logger.error(
                    f"[FUND-ERR] [{idx}/{total}] {isin} — {exc} "
                    f"(in {elapsed_ms:.0f}ms)\n{traceback.format_exc()}"
                )
                # Never abort the batch for one fund's error
                continue

        # -- Alarm engine (cross-seccional, post-loop) -----------------
        # v28 memory fix: read ONLY the latest date per (metric,window,real_flag)
        # instead of the full fund_metric_timeseries table (~14 M rows / 5 GB).
        # pctile_self is now computed per-fund in the loop above;
        # this block only needs to produce pctile_cat, zscore_cat, and alerts.
        # Guard: skip on --dry-run (diagnostic runs must not load the cross-fund data).
        if _want("rolling") and ROLLING_STATS_ENABLED and not dry_run:
            logger.info(
                "[ROLLING] Calculando snapshot cross-seccional (ultima fecha)..."
            )
            try:
                # Latest-date-only join — served by idx_fmts_metric_window_real_date.
                # Returns ~(fondos × 3 metrics × 5 windows × 2 flags) rows ≈ few-MB,
                # regardless of how large the full timeseries history is.
                latest_df = pd.read_sql(
                    """SELECT t.isin, t.metric, t.window, t.date,
                              t.value, t.real_flag, m.Fund_Nature
                       FROM fund_metric_timeseries t
                       JOIN (
                           SELECT metric, window, real_flag, MAX(date) AS mx
                           FROM fund_metric_timeseries
                           WHERE metric IN (
                               'roll_vol_ann','roll_max_dd','roll_return_ann'
                           )
                           GROUP BY metric, window, real_flag
                       ) latest
                         ON  t.metric    = latest.metric
                         AND t.window    = latest.window
                         AND t.real_flag = latest.real_flag
                         AND t.date      = latest.mx
                       LEFT JOIN fund_master m ON t.isin = m.ISIN
                       WHERE t.metric IN (
                           'roll_vol_ann','roll_max_dd','roll_return_ann'
                       )""",
                    conn
                )
                if not latest_df.empty:
                    # compute_category_snapshot expects 'date' column —
                    # latest_df already has it (the latest date only).
                    cat_df = compute_category_snapshot(latest_df, min_peers=5)

                    # Cat signals (pctile_cat, zscore_cat) extracted directly
                    # from cat_df — no full-history pass needed (RC-2 fix).
                    cat_sig_rows = cat_signals_from_snapshot(cat_df)
                    cat_by: dict[tuple, list] = {}
                    for r in cat_sig_rows:
                        key = (r["isin"], r["window"])
                        cat_by.setdefault(key, []).append({
                            "metric":      r["metric"],
                            "value":       r["value"],
                            "real_flag":   r["real_flag"],
                            "source_rows": r.get("source_rows"),
                        })
                    n_cat = 0
                    for (s_isin, s_window), s_rows in cat_by.items():
                        n_cat += _write_metrics(
                            conn, s_isin, s_rows, s_window, dry_run
                        )
                    logger.info(
                        f"[ROLLING] {len(cat_sig_rows)} señales de categoria → "
                        f"{n_cat} escritas en fund_metrics"
                    )

                    al_rows  = compute_alerts(cat_df, ALERT_RULES, min_peers=5)
                    n_alerts = _write_metric_alerts(conn, al_rows, dry_run)
                    logger.info(
                        f"[ROLLING] {len(al_rows)} alertas evaluadas, "
                        f"{n_alerts} escritas"
                    )
                else:
                    logger.info(
                        "[ROLLING] fund_metric_timeseries vacía — "
                        "snapshots de categoria y alertas omitidos"
                    )
            except Exception as exc:
                logger.warning(
                    f"[ROLLING] Motor rolling falló (no fatal): {exc}\n"
                    f"{traceback.format_exc()}"
                )

    except Exception as exc:
        status = "ERROR"
        logger.critical(
            f"[PIPELINE] Error fatal no controlado: {exc}\n"
            f"{traceback.format_exc()}"
        )
        raise

    finally:
        elapsed_total = time.time() - t_run_start
        logger.info(
            f"[RUN END] run_id={run_id} status={status} "
            f"processed={n_processed} skipped={n_skipped} errors={n_errors} "
            f"total_written={total_written} elapsed={elapsed_total:.0f}s"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _allow_sleep()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline de calculo P2 (v27 — fault-tolerant, timestamped, idempotent)"
    )
    parser.add_argument(
        "--isin", default=None,
        help="Procesar solo este/estos ISINs (coma-separado, p.ej. LU0070214613,IE0001257090)"
    )
    parser.add_argument(
        "--horizon", default=None,
        help="Solo este horizonte (ej: since_inception, rolling_1y)"
    )
    parser.add_argument(
        "--metrics", default=None,
        help=(
            "Familias de metricas a calcular, separadas por coma. "
            f"Disponibles: {', '.join(sorted(_ALL_METRIC_FAMILIES))}. "
            "Si se omite, se calculan todas las familias."
        )
    )
    parser.add_argument(
        "--from-date", default=None, dest="from_date",
        help="Recortar NAV desde esta fecha antes de calcular (YYYY-MM-DD). "
             "Recomendado usar con --force para evitar cache hit."
    )
    parser.add_argument(
        "--to-date", default=None, dest="to_date",
        help="Recortar NAV hasta esta fecha antes de calcular (YYYY-MM-DD). "
             "Recomendado usar con --force para evitar cache hit."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignorar cache de input-hash y recalcular siempre. "
             "Usar tras cambios en la logica de calculo o con --from-date/--to-date."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Calcula pero no escribe en DB (modo diagnostico)"
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Legado: salta ISINs ya procesados hoy. "
             "Preferir el hash-skip automatico sobre este flag."
    )
    args = parser.parse_args()

    run(
        isins=[i.strip().upper() for i in args.isin.split(",") if i.strip()]
               if args.isin else None,
        horizons_filter=[args.horizon] if args.horizon else None,
        metrics_filter=[m.strip() for m in args.metrics.split(",") if m.strip()]
                       if args.metrics else None,
        from_date=args.from_date,
        to_date=args.to_date,
        force=args.force,
        dry_run=args.dry_run,
        resume=args.resume,
    )
