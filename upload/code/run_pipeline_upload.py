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
from shared.db import get_connection
from src.loaders.nav_loader import load_nav, get_isins_with_nav
from src.loaders.inflation_loaders import load_ipc, ipc_available
from src.calculations.risk_metrics import compute_risk_metrics
from src.calculations.consistency import consistency_metrics
from src.calculations.macro_sensitivity import (
    load_macro_factors, compute_macro_sensitivity
)
from src.calculations.regime_returns import (
    load_regime_history, compute_regime_returns
)
from src.calculations.momentum import compute_momentum
from src.calculations.capture_ratios import compute_capture_ratios
from src.calculations.persistence import compute_persistence
from src.calculations.currency_factor import compute_currency_factor
from src.utils.validators import validate_nav, validate_ipc
from src.utils.time_windows import slice_window


# ============================================================
# Helpers de escritura
# ============================================================

def _write_metrics(
    conn: sqlite3.Connection,
    isin: str,
    metrics: list[dict],
    horizon: str,
    dry_run: bool,
) -> int:
    """Persiste lista de metricas en fund_metrics. Devuelve nº escritas."""
    if not metrics or dry_run:
        return 0

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
            METRIC_VERSION,
            m.get("source_rows"),
        )
        for m in metrics
    ]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


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
    conn.execute(
        """INSERT INTO p2_pipeline_log
               (isin, step, status, horizon, metric_version, message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (isin, step, status, horizon, METRIC_VERSION, message),
    )
    conn.commit()


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
        print("AVISO: No hay factores macro. Ejecuta macro_loader antes de P2.")
    else:
        print(f"Factores macro: {list(macro_df.columns)} ({len(macro_df)} meses)")

    # -- Cargar historico de regimenes (una vez para todos los fondos) --
    regime_df = load_regime_history(conn)
    if regime_df.empty:
        print("AVISO: Sin historico de regimenes. "
              "Ejecuta macro_loader y m2_global_builder antes de P2.")

    # -- Universo de ISINs -------------------------------------
    if isins is None:
        isins = get_isins_with_nav(conn)

    if not isins:
        print("No hay ISINs con datos NAV en fund_nav_monthly. Pipeline finalizado.")
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
        for horizon_name, months in ROLLING_WINDOWS.items():
            if horizons_filter and horizon_name not in horizons_filter:
                continue
            nav_w = nav_df.tail(months).reset_index(drop=True)
            ipc_w = ipc_df.tail(months).reset_index(drop=True) if ipc_df is not None else None
            if len(nav_w) < MIN_NAV_ROWS:
                continue
            isin_written += _process_horizon(
                isin, nav_w, ipc_w, horizon_name, conn, dry_run
            )

        # -- Atributos del fondo (usados en multiples modulos) --
        _fm = conn.execute(
            "SELECT Fund_Nature FROM fund_master WHERE ISIN=?", (isin,)
        ).fetchone()
        fund_nature = _fm[0] if _fm else None

        # -- Sensibilidad macro ----------------------------------
        if not macro_df.empty and len(nav_df) >= 36:

            sens_list = compute_macro_sensitivity(nav_df, macro_df)
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
        if fund_nature and len(nav_df) >= 84:  # minimo 7 anos para ventanas rolling
            per_list = compute_persistence(isin, fund_nature, nav_df, conn)
            per_rows = [{"metric": m, "value": v, "real_flag": rf,
                         "source_rows": len(nav_df)}
                        for m, v, rf in per_list]
            isin_written += _write_metrics(
                conn, isin, per_rows, "since_inception", dry_run)

        # -- Factor divisa ----------------------------------------
        fund_info = conn.execute(
            "SELECT Fund_Currency, Hedging_Policy FROM fund_master WHERE ISIN=?",
            (isin,)
        ).fetchone()
        if fund_info:
            fund_currency   = fund_info[0]
            hedging_policy  = fund_info[1]
            fx_list = compute_currency_factor(
                isin, fund_currency, hedging_policy, nav_df, conn)
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

        total_written += isin_written
        print(f"  [{idx}/{len(isins)}] {isin} -> {isin_written} metricas")

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
    args = parser.parse_args()

    run(
        isins=[args.isin] if args.isin else None,
        horizons_filter=[args.horizon] if args.horizon else None,
        dry_run=args.dry_run,
    )
