# proyecto2/src/pipeline/run_pipeline.py
# -*- coding: utf-8 -*-
"""
Pipeline canonico de Proyecto 2.

Proceso por fondo:
  1. Cargar serie NAV desde fund_nav_monthly
  2. Cargar IPC desde series_inflation
  3. Calcular metricas de riesgo + consistencia (nominal y real)
  4. Calcular para horizonte since_inception y ventanas de crisis
  5. Calcular horizontes rolling si hay suficientes datos
  6. Persistir en fund_metrics
  7. Registrar trazabilidad en p2_pipeline_log

Uso:
    cd c:/desarrollo/fondos/proyecto2
    python -m src.pipeline.run_pipeline

    Opciones:
    --isin LU1234567890          procesar solo un ISIN (modo debug)
    --horizon since_inception    solo ese horizonte
    --dry-run                    calcula pero no escribe en DB
"""

import argparse
import sqlite3
import sys
import time
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
)


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

def run(
    isins: list[str] | None = None,
    horizons_filter: list[str] | None = None,
    dry_run: bool = False,
    resume: bool = False,
) -> None:

    conn = get_connection()

    # -- Cargar IPC --------------------------------------------
    if not ipc_available(conn, REGION_IPC):
        print(
            f"AVISO: No hay datos IPC para '{REGION_IPC}' en series_inflation.\n"
            "Se calcularan solo metricas nominales.\n"
            "Carga el IPC con el loader de series_macro antes de ejecutar P2."
        )
        ipc_df = None
    else:
        ipc_df = load_ipc(conn, REGION_IPC)
        ok, err = validate_ipc(ipc_df)
        if not ok:
            print(f"AVISO: IPC invalido ({err}) -- se usaran solo metricas nominales")
            ipc_df = None

    # -- Cargar factores macro (una vez para todos los fondos) --
    macro_df = load_macro_factors(conn)
    if macro_df.empty:
        print("AVISO: No hay factores macro. Ejecuta macro_discovery antes de P2.")
    else:
        print(f"Factores macro: {list(macro_df.columns)} ({len(macro_df)} meses)")
        
        # -- Auto-build M2 Global si no existe aún ----------------------
        if "m2_global_yoy" not in macro_df.columns:
            print("  [M2_Global] No detectado en factores. Construyendo...")
            rows_built = build_m2_global(conn, dry_run=dry_run)
            if rows_built > 0:
                print(f"  [M2_Global] {rows_built} registros construidos. Recargando factores...")
                macro_df = load_macro_factors(conn)
            else:
                print("  [M2_Global] No se pudo construir (datos insuficientes)")

    # -- Cargar historico de regimenes (una vez para todos los fondos) --
    regime_df = load_regime_history(conn)
    if regime_df.empty:
        print("AVISO: Sin historico de regimenes. "
              "Ejecuta macro_discovery y m2_global_builder antes de P2.")

    # -- Universo de ISINs -------------------------------------
    if isins is None:
        isins = get_isins_with_nav(conn)

    if not isins:
        print("No hay ISINs con datos NAV en fund_nav_monthly. Pipeline finalizado.")
        conn.close()
        return

    if resume and not dry_run:
        today = date.today().isoformat()
        done = {r[0] for r in conn.execute(
            "SELECT DISTINCT isin FROM fund_metrics "
            "WHERE calculation_date = ? AND metric_version = ?",
            (today, METRIC_VERSION)
        ).fetchall()}
        # ISINs marcados para recalcular métricas nunca se saltan (v25)
        force_recalc = {r[0] for r in conn.execute(
            "SELECT isin FROM nav_sources WHERE data_status='RECALCULATE_METRICS'"
        ).fetchall()}
        isins = [i for i in isins if i not in done or i in force_recalc]
        print(f"  [Resume] {len(done)} fondos ya procesados hoy -> saltados. "
              f"{len(force_recalc)} con RECALCULATE_METRICS forzados. "
              f"{len(isins)} pendientes.")
        if not isins:
            print("Pipeline completado (todos los fondos ya procesados).")
            conn.close()
            return

    print(f"Procesando {len(isins)} fondos | dry_run={dry_run} | "
          f"IPC={'SI' if ipc_df is not None else 'NO'}")

    total_written = 0

    for idx, isin in enumerate(isins, 1):
        nav_df = load_nav(conn, isin)
        if nav_df.empty:
            print("-> sin NAV, saltado")
            _log(conn, isin, "NAV_LOAD", "SKIP", None, "Sin datos NAV", dry_run)
            continue

        ok, err = validate_nav(nav_df)
        if not ok:
            print(f"-> NAV invalido ({err}), saltado")
            _log(conn, isin, "NAV_LOAD", "WARN", None, err, dry_run)
            continue

        isin_written = 0

        # -- Since inception -----------------------------------
        if horizons_filter is None or "since_inception" in horizons_filter:
            isin_written += _process_horizon(
                isin, nav_df, ipc_df, "since_inception", conn, dry_run
            )

        # -- Ventanas de crisis --------------------------------
        for crisis_name, (start, end) in CRISIS_WINDOWS.items():
            if horizons_filter and crisis_name not in horizons_filter:
                continue
            nav_w = slice_window(nav_df, start, end)
            ipc_w = slice_window(ipc_df, start, end) if ipc_df is not None else None
            if len(nav_w) < MIN_NAV_ROWS:
                continue
            isin_written += _process_horizon(
                isin, nav_w, ipc_w, crisis_name, conn, dry_run
            )

        # -- Horizontes rolling --------------------------------
        # REL-3: date-based slicing (tail(N) puede excluir meses con retraso puntual
        # de publicación si los registros más recientes tienen fechas adelantadas).
        for horizon_name, months in ROLLING_WINDOWS.items():
            if horizons_filter and horizon_name not in horizons_filter:
                continue
            _cutoff = nav_df["date"].max() - pd.DateOffset(months=months)
            nav_w = nav_df[nav_df["date"] > _cutoff].reset_index(drop=True)
            ipc_w = (
                ipc_df[ipc_df["date"] > _cutoff].reset_index(drop=True)
                if ipc_df is not None else None
            )
            if len(nav_w) < MIN_NAV_ROWS:
                continue
            isin_written += _process_horizon(
                isin, nav_w, ipc_w, horizon_name, conn, dry_run
            )

        # -- Horizontes cortos diarios (v24) --------------------------
        # Requiere fund_nav_daily; graceful no-op si vacío.
        nav_daily = load_nav_daily(conn, isin)
        if not nav_daily.empty:
            for sh_name, sh_days in SHORT_WINDOWS.items():
                if horizons_filter and sh_name not in horizons_filter:
                    continue
                nav_sh = nav_daily.tail(sh_days).reset_index(drop=True)
                min_obs = SHORT_WINDOW_MIN_OBS.get(sh_name, 15)
                if len(nav_sh) < min_obs:
                    _log(conn, isin, "CALC", "SKIP", sh_name,
                         f"Solo {len(nav_sh)} filas NAV diario (min {min_obs})", dry_run)
                    continue
                sh_list = compute_short_horizon_metrics(nav_sh, ipc_df)
                sh_rows = [{"metric": m, "value": v, "real_flag": rf,
                            "source_rows": len(nav_sh)}
                           for m, v, rf in sh_list]
                isin_written += _write_metrics(
                    conn, isin, sh_rows, sh_name, dry_run,
                    metric_version=METRIC_VERSION_SHORT
                )
                if sh_rows:
                    _log(conn, isin, "CALC", "OK", sh_name,
                         f"{len(sh_rows)} metricas cortas (d1)", dry_run)

        # -- Atributos del fondo (lectura unica para todos los modulos) --
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

        # -- Sensibilidad macro ----------------------------------
        if not macro_df.empty and len(nav_df) >= MIN_NAV_MACRO:

            sens_list = compute_macro_sensitivity(
                nav_df, macro_df,
                geography=geography,
                development_status=development_status,
            )
            sens_rows = [{"metric": m, "value": v, "real_flag": rf,
                          "source_rows": len(nav_df)}
                         for m, v, rf in sens_list]
            isin_written += _write_metrics(
                conn, isin, sens_rows, "since_inception", dry_run)

            if fund_nature:
                mom_list = compute_momentum(isin, fund_nature, nav_df, conn)
                mom_rows = [{"metric": m, "value": v, "real_flag": rf,
                             "source_rows": len(nav_df)}
                            for m, v, rf in mom_list]
                isin_written += _write_metrics(
                    conn, isin, mom_rows, "since_inception", dry_run)

                cap_list = compute_capture_ratios(isin, fund_nature, nav_df, conn)
                cap_rows = [{"metric": m, "value": v, "real_flag": rf,
                             "source_rows": len(nav_df)}
                            for m, v, rf in cap_list]
                isin_written += _write_metrics(
                    conn, isin, cap_rows, "since_inception", dry_run)

        # -- Persistencia del alpha ------------------------------
        if fund_nature and len(nav_df) >= MIN_NAV_PERSIST:  # minimo 7 anos
            per_list = compute_persistence(isin, fund_nature, nav_df, conn)
            per_rows = [{"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in per_list]
            isin_written += _write_metrics(
                conn, isin, per_rows, "since_inception", dry_run)

        # -- Factor divisa ----------------------------------------
        fx_list = compute_currency_factor(
            isin, fund_currency, hedging_policy, nav_df, conn,
            asset_currency=asset_currency,
        )
        fx_rows = [{"metric": m, "value": v, "real_flag": rf,
                    "source_rows": len(nav_df)}
                   for m, v, rf in fx_list]
        isin_written += _write_metrics(
            conn, isin, fx_rows, "since_inception", dry_run)

        # -- Retornos por regimen ----------------------------------
        if not regime_df.empty and len(nav_df) >= 36:
            reg_list = compute_regime_returns(nav_df, regime_df)
            reg_rows = [{"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in reg_list]
            isin_written += _write_metrics(
                conn, isin, reg_rows, "since_inception", dry_run)

        # -- Indicadores rolling (v26 — ROLLING_STATS_ENABLED) ---------
        if ROLLING_STATS_ENABLED:
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

        total_written += isin_written
        print(f"  [{idx}/{len(isins)}] {isin} -> {isin_written} metricas")

        # v25: consumir flag RECALCULATE_METRICS una vez calculadas
        if isin_written > 0 and not dry_run:
            conn.execute(
                "UPDATE nav_sources SET data_status='OK' "
                "WHERE isin=? AND data_status='RECALCULATE_METRICS'",
                (isin,)
            )

    # -- Alarm engine (cross-seccional, post-loop) -----------------
    # Requiere que fund_metric_timeseries esté poblada; se ejecuta
    # después de procesar todos los ISINs para tener el universo completo.
    if ROLLING_STATS_ENABLED:
        print("\n[ROLLING] Calculando snapshots y alertas cross-seccionales...")
        try:
            ts_df = pd.read_sql(
                """SELECT t.isin, t.metric, t.window, t.date, t.value, t.real_flag,
                          m.Fund_Nature
                   FROM fund_metric_timeseries t
                   LEFT JOIN fund_master m ON t.isin = m.ISIN
                   WHERE t.metric IN ('roll_vol_ann','roll_max_dd','roll_return_ann')""",
                conn
            )
            if not ts_df.empty:
                # Category snapshot (cross-seccional, última fecha)
                cat_df = compute_category_snapshot(ts_df, min_peers=5)

                # P2: latest-scalar snapshot → fund_metrics
                snap_rows = compute_timeseries_snapshots(ts_df, cat_df, min_self_obs=12)
                # Agrupar por (isin, window) para llamar _write_metrics
                snap_by = {}
                for r in snap_rows:
                    key = (r["isin"], r["window"])
                    snap_by.setdefault(key, []).append(
                        {"metric": r["metric"], "value": r["value"],
                         "real_flag": r["real_flag"], "source_rows": r.get("source_rows")}
                    )
                n_snap = 0
                for (s_isin, s_window), s_rows in snap_by.items():
                    n_snap += _write_metrics(conn, s_isin, s_rows, s_window, dry_run)
                print(f"[ROLLING] {len(snap_rows)} señales derivadas → {n_snap} escritas en fund_metrics")

                # P3: alarm engine
                al_rows  = compute_alerts(cat_df, ALERT_RULES, min_peers=5)
                n_alerts = _write_metric_alerts(conn, al_rows, dry_run)
                print(f"[ROLLING] {len(al_rows)} alertas evaluadas, {n_alerts} escritas")
            else:
                print("[ROLLING] fund_metric_timeseries vacía — snapshots y alertas omitidos")
        except Exception as exc:
            print(f"[ROLLING] WARN motor rolling falló (no fatal): {exc}")

    conn.close()
    print(f"\nPipeline completado. Total metricas escritas: {total_written}")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de calculo P2")
    parser.add_argument("--isin",    default=None, help="Procesar solo este ISIN")
    parser.add_argument("--horizon", default=None, help="Solo este horizonte")
    parser.add_argument("--dry-run", action="store_true",
                        help="Calcula pero no escribe en DB")
    parser.add_argument("--resume", action="store_true",
                        help="Salta ISINs ya procesados hoy (para reanudar tras crash)")
    args = parser.parse_args()

    run(
        isins=[i.strip().upper() for i in args.isin.split(",") if i.strip()] if args.isin else None,
        horizons_filter=[args.horizon] if args.horizon else None,
        dry_run=args.dry_run,
        resume=args.resume,
    )
