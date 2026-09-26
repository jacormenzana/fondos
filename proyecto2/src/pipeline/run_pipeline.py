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

--- Two-timestamp model (architectural invariant) ---
fund_metrics.load_ts            — Per-VALUE change stamp (data lineage). Advances only when a
                                  row is physically rewritten by INSERT OR REPLACE. Heterogeneous
                                  values within a single run are EXPECTED under cadence/hash-skip
                                  optimisations (e.g. EFF-1 OLS quarterly cadence leaves beta_*
                                  rows at their last-computed date; hash-skipped funds are not
                                  rewritten at all). Never touch this column to fake uniformity —
                                  a timestamp that misrepresents the computation date breaks
                                  lineage tracing.
fund_metric_state.calculated_at — Per-FUND run stamp (orchestration). Advances every cycle the
                                  pipeline touches the fund (isin_written > 0), including runs
                                  where only risk/rolling metrics were rewritten and OLS was
                                  cadence-skipped. This is the authoritative "fund processed on
                                  DATE" signal for downstream audits.

Audit classification rule for stale load_ts:
  EXPECTED  — metric is in the OLS-cadence set (beta_*, energy_sensitivity_pct,
               hy_spread_sensitivity_pct) AND fund_metric_state.calculated_at advanced today.
  ANOMALY   — stale row outside that set, OR calculated_at did NOT advance (silent write failure).
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
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

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
from shared.config import MIN_PEERS, REGIME_MIN_NAV_TOTAL
from shared.config import (
    ROLLING_STATS_ENABLED, ALERT_RULES, ROLLING_TIMESERIES_METRICS
)
from shared.db import get_connection, is_postgres_connection, in_transaction, begin_immediate, executemany
from src.readers.db_readers import (
    load_nav, get_isins_with_nav, load_ipc, ipc_available, load_nav_daily,
    load_rf_rate,                    # §4g — historical risk-free rate (€STR proxy)
    count_isins_with_new_nav,        # P2-04 preflight
    load_ts_cohort,                  # observability: load_ts cohort (two-timestamp model)
    count_stale_nav_funds,           # observability: NAV-staleness gate
    coverage_snapshot,               # observability: P3-consumed metric coverage
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
    resolve_rf_rate,
)
from src.utils.fingerprint import compute_input_hash
from src.utils.logger import get_pipeline_logger
from src.writers.metrics_writer import (
    rows_from_metric_tuples as _rows_from_metric_tuples,
    write_metrics as _write_metrics,
    write_timeseries as _write_timeseries,
    replace_beta_set as _replace_beta_set,
)


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
    ph = "%s" if is_postgres_connection(conn) else "?"
    row = conn.execute(
        f"SELECT input_hash FROM fund_metric_state "
        f"WHERE isin={ph} AND metric_version={ph}",
        (isin, METRIC_VERSION),
    ).fetchone()
    return row[0] if row else None


def _upsert_metric_state(
    conn: sqlite3.Connection,
    isin: str,
    input_hash: str,
    dry_run: bool,
) -> None:
    """Persiste o actualiza el input_hash en fund_metric_state.

    EFF-2: skips own BEGIN/COMMIT when caller already has an open transaction.
    """
    if dry_run:
        return
    if is_postgres_connection(conn):
        sql = (
            "INSERT INTO fund_metric_state (isin, metric_version, input_hash, calculated_at)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (isin, metric_version) DO UPDATE SET"
            " input_hash = excluded.input_hash, calculated_at = excluded.calculated_at"
        )
    else:
        sql = (
            "INSERT OR REPLACE INTO fund_metric_state"
            " (isin, metric_version, input_hash, calculated_at)"
            " VALUES (?, ?, ?, ?)"
        )
    args = (isin, METRIC_VERSION, input_hash, date.today().isoformat())
    # EFF-2: skip own transaction when the caller batches for us
    if in_transaction(conn):
        conn.execute(sql, args)
        return
    # Postgres migration addendum (2026-09-20, Stage 4): `except sqlite3.OperationalError` below is
    # deliberately left SQLite-only, not a gap. Verified live: a genuine Postgres error here
    # (tested with both a StringDataRightTruncation and a real ForeignKeyViolation) is a
    # psycopg.errors.* type, which this except clause does not match — it propagates straight
    # through, past the inner `except Exception: ROLLBACK; raise` (already dialect-generic) and
    # out of this function, exactly like SQLite's own "else: raise" branch for a non-lock error.
    # Also verified the connection survives clean and stays usable afterward. No retry-on-lock
    # loop is needed for Postgres in the first place — begin_immediate()'s own docstring already
    # explains why (MVCC + row locks block rather than raise), so there is nothing to add here.
    for attempt in range(3):
        try:
            begin_immediate(conn)
            try:
                conn.execute(sql, args)
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
# OLS quarterly-cadence helpers (EFF-1)
# ============================================================

def _ols_is_fresh(
    conn: sqlite3.Connection,
    isin: str,
    nav_count: int,
    current_quarter: str,
    force: bool = False,
) -> bool:
    """True if OLS betas are still fresh: computed this quarter and NAV grew < 3 rows.

    When force=True always returns False so --force guarantees a full OLS recompute
    (not just a hash-cache bypass) even within the same quarter.

    When this returns True the beta set is NOT rewritten, so its load_ts stays
    at the date it was last computed.  This is EXPECTED behaviour under the
    two-timestamp model: load_ts = per-value change stamp, not a run stamp.
    See module docstring for the full model and audit classification rules.
    """
    if force:
        return False
    ph = "%s" if is_postgres_connection(conn) else "?"
    row = conn.execute(
        f"SELECT last_ols_quarter, last_ols_nav_count FROM fund_metric_state "
        f"WHERE isin={ph} AND metric_version={ph}",
        (isin, METRIC_VERSION),
    ).fetchone()
    if not row or row[0] is None:
        return False
    return row[0] == current_quarter and (nav_count - (row[1] or 0)) < 3


def _update_ols_state(
    conn: sqlite3.Connection,
    isin: str,
    current_quarter: str,
    nav_count: int,
    dry_run: bool,
) -> None:
    """Record that OLS was computed for this fund in current_quarter."""
    if dry_run:
        return
    ph = "%s" if is_postgres_connection(conn) else "?"
    conn.execute(
        f"UPDATE fund_metric_state SET last_ols_quarter={ph}, last_ols_nav_count={ph} "
        f"WHERE isin={ph} AND metric_version={ph}",
        (current_quarter, nav_count, isin, METRIC_VERSION),
    )


# ============================================================
# Helpers de escritura
# ============================================================
# rows_from_metric_tuples / write_metrics / write_timeseries / replace_beta_set
# viven en src.writers.metrics_writer (P0, 2026-09-15) -- extraidos para ser
# testables bajo R-7 sin importar este modulo. Importados arriba con sus
# nombres historicos (_write_metrics etc.) para no tocar cada call site.


@dataclass(frozen=True)
class _FundMetricCtx:
    """Per-fund state shared by the metric-family registry below (P2
    canonicalización, 2026-09-14). Bundles what each compute_xxx() call
    needs so MetricFamilySpec entries can stay declarative — no per-family
    argument marshalling scattered through the per-fund loop."""
    isin: str
    conn: sqlite3.Connection
    nav_df: pd.DataFrame
    fund_nature: str | None
    fund_currency: str | None
    hedging_policy: str | None
    asset_currency: str | None
    regime_df: pd.DataFrame
    has_macro: bool


@dataclass(frozen=True)
class MetricFamilySpec:
    """Declarative per-fund metric family: name (the --metrics/_want() key),
    fn (ctx -> list[(metric, value, real_flag)]), and the gates deciding
    whether it runs for a given fund. Gate fields are plain data, in the
    same spirit as shared/config.py's threshold catalog (ALERT_RULES etc.)
    — adding a metric family means one list entry in
    _PER_FUND_METRIC_FAMILIES, not a copy-pasted dispatch block (this
    exact block was duplicated 7x before the 2026-09-14 refactor).

    Scope note: only families with a uniform "guard + compute + write via
    _write_metrics/_replace_beta_set" shape are registry-driven (momentum,
    capture, persistence, fx, regime). risk/consistency/srri (DataFrame
    return contract), short-horizon (nested per-window loop with its own
    logging), macro sensitivity (OLS quarterly-cadence gate, EFF-1), and
    rolling/alerts (cross-sectional, multi-stage) stay as explicit code —
    forcing genuinely different shapes into one interface would reduce
    clarity, not improve it.
    """
    name: str
    fn: Callable[["_FundMetricCtx"], list[tuple]]
    horizon: str = "since_inception"
    min_obs: int | None = None
    requires_fund_nature: bool = False
    requires_has_macro: bool = False
    requires_nonempty_regime: bool = False
    writer: Callable[..., int] = _write_metrics


_PER_FUND_METRIC_FAMILIES: list[MetricFamilySpec] = [
    MetricFamilySpec(
        "momentum",
        lambda ctx: compute_momentum(ctx.isin, ctx.fund_nature, ctx.nav_df, ctx.conn),
        requires_fund_nature=True, requires_has_macro=True,
    ),
    MetricFamilySpec(
        "capture",
        lambda ctx: compute_capture_ratios(ctx.isin, ctx.fund_nature, ctx.nav_df, ctx.conn),
        requires_fund_nature=True, requires_has_macro=True,
    ),
    MetricFamilySpec(
        "persistence",
        lambda ctx: compute_persistence(ctx.isin, ctx.fund_nature, ctx.nav_df, ctx.conn),
        requires_fund_nature=True, min_obs=MIN_NAV_PERSIST,
    ),
    MetricFamilySpec(
        "fx",
        lambda ctx: compute_currency_factor(
            ctx.isin, ctx.fund_currency, ctx.hedging_policy, ctx.nav_df, ctx.conn,
            asset_currency=ctx.asset_currency,
        ),
    ),
    MetricFamilySpec(
        "regime",
        lambda ctx: compute_regime_returns(ctx.nav_df, ctx.regime_df),
        min_obs=REGIME_MIN_NAV_TOTAL, requires_nonempty_regime=True,
    ),
]


def _run_metric_family(
    spec: MetricFamilySpec,
    ctx: _FundMetricCtx,
    dry_run: bool,
    want_fn: Callable[[str], bool],
) -> int:
    """Evaluate one MetricFamilySpec's gates against ctx; if they pass,
    compute and write. Returns rows written (0 if gated out or _want()=False)."""
    if not want_fn(spec.name):
        return 0
    if spec.requires_fund_nature and not ctx.fund_nature:
        return 0
    if spec.requires_has_macro and not ctx.has_macro:
        return 0
    if spec.requires_nonempty_regime and ctx.regime_df.empty:
        return 0
    if spec.min_obs is not None and len(ctx.nav_df) < spec.min_obs:
        return 0
    result = spec.fn(ctx)
    rows = _rows_from_metric_tuples(result, len(ctx.nav_df))
    return spec.writer(
        ctx.conn, ctx.isin, rows, spec.horizon, dry_run,
        algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID, metric_version=METRIC_VERSION,
    )


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
    if is_postgres_connection(conn):
        sql = """
            INSERT INTO fund_metric_alerts
                (isin, metric, window_label, level, rule_code,
                 value, reference_value, ref_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (isin, metric, window_label) DO UPDATE SET
                level = excluded.level, rule_code = excluded.rule_code,
                value = excluded.value, reference_value = excluded.reference_value,
                ref_type = excluded.ref_type, detected_at = DEFAULT
        """
    else:
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
    # `except sqlite3.OperationalError` below: SQLite-only by design, verified safe under Postgres
    # — see _upsert_metric_state()'s comment above for the full reasoning (same retry-wrapper shape).
    for attempt in range(5):
        try:
            begin_immediate(conn)
            try:
                executemany(conn, sql, data)
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
    """EFF-2: skips own BEGIN/COMMIT when caller already has an open transaction."""
    if dry_run:
        return
    ph = "%s" if is_postgres_connection(conn) else "?"
    sql = (
        "INSERT INTO p2_pipeline_log"
        " (isin, step, status, horizon, metric_version, message, batch_id)"
        f" VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
    )
    args = (isin, step, status, horizon, METRIC_VERSION, message, RUN_BATCH_ID)
    # EFF-2: skip own transaction when the caller batches for us
    if in_transaction(conn):
        conn.execute(sql, args)
        return
    # `except sqlite3.OperationalError` below: SQLite-only by design, verified safe under Postgres
    # — see _upsert_metric_state()'s comment above for the full reasoning (same retry-wrapper shape).
    for attempt in range(5):
        try:
            begin_immediate(conn)
            try:
                conn.execute(sql, args)
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
    rf_rate_df: pd.DataFrame | None = None,
) -> int:
    """Calcula y persiste todas las metricas para un horizonte dado."""
    if len(nav_df) < MIN_NAV_ROWS:
        _log(conn, isin, "CALC", "SKIP", horizon,
             f"Solo {len(nav_df)} filas NAV (minimo {MIN_NAV_ROWS})", dry_run)
        return 0

    # -- Tipo libre de riesgo alineado a la fecha fin de este horizonte -----
    # AUDITORIA_ESTADISTICA.md §2.9 (2026-09-13): esta llamada usaba antes
    # RISK_FREE_RATE_ANN plana mientras compute_rolling_rows() (write-path de
    # fund_metric_timeseries) ya usaba rf_series alineada por fecha (§4g) —
    # una asimetria de FORMULA real entre los dos write-paths de sharpe/
    # sortino (SCALAR_EQUALS_TIMESERIES), no solo staleness de datos.
    # resolve_rf_rate replica el mismo ffill/bfill para un solo punto.
    rf_for_horizon = resolve_rf_rate(nav_df["date"].max(), rf_rate_df, RISK_FREE_RATE_ANN)

    # -- Metricas de riesgo ------------------------------------
    risk_df = compute_risk_metrics(nav_df, ipc_df, rf_for_horizon)
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
    written = _write_metrics(
        conn, isin, all_metrics, horizon, dry_run,
        algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID, metric_version=METRIC_VERSION,
    )
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
CALC_VERSION: str = "20260918"  # v35: deflate_nav() switched from
# reindex+ffill+bfill to pd.merge_asof(direction='backward'), and
# run_pipeline.py stopped pre-slicing ipc_df to each window before calling
# it. v34's reindex-based fix (previous CALC_VERSION) closed the systemic
# inner-join bug but left a second, narrower one: reindex(nav_df['date'])
# can only ffill using values that survive the reindex, so a window's own
# FIRST date (if it doesn't exactly match an IPC date) had no earlier
# anchor to propagate from and silently bfilled a LATER ipc value instead —
# this broke SCALAR_EQUALS_TIMESERIES specifically for rolling_3y (not
# 1y/2y/5y/10y, whose windows more often happened to start on an
# ipc-aligned date). merge_asof searches ipc_df's actual range regardless
# of reindex-target overlap, and the caller no longer restricts that range.
# Also fixed the same inner-join bug in consistency.py::consistency_metrics
# (it had reimplemented deflation locally instead of calling deflate_nav).

# ── v26 audit columns ──────────────────────────────────────────────────────
# RUN_BATCH_ID is set once at the start of run() and written to every Gold row
# produced in that invocation. It correlates fund_metrics / fund_metric_timeseries
# rows and p2_pipeline_log entries back to a single pipeline run.
# Initialized here as a sentinel; actual value is always set before any write.
RUN_BATCH_ID: str = ""


def _new_batch_id() -> str:
    """Return a unique, human-readable batch id for the current run.

    Format: ``P2-YYYYMMDD_HHMMSS-<6-hex>``
    Pure function (no side effects) — easy to unit-test (R-7).
    """
    return f"P2-{datetime.now():%Y%m%d_%H%M%S}-{uuid4().hex[:6]}"

# P3-consumed metric surface — used by [COVERAGE] observability line to baseline
# distinct-ISIN coverage across runs. Update this tuple whenever P3's scoring
# model adds or removes a consumed metric. Source of truth: AGENTS.md §P3 Layer 2.
#
# NOTE: P3 fund_scorer loads return_ann with real_flag=1 and renames it to
# return_ann_real internally.  coverage_snapshot() queries by DB metric name;
# "return_ann" (no suffix) covers both nominal and real rows and gives the
# correct ISIN count.  Do NOT use "return_ann_real" here — it is a P3-internal
# alias, not a metric stored in fund_metrics.
_P3_CONSUMED_METRICS: tuple[str, ...] = (
    "return_ann",          # DB name; P3 reads real_flag=1 and aliases → return_ann_real
    "sharpe",
    "max_dd",
    "alpha_persistence",
    "capture_ratio",
    "momentum_rank",
)


def _refresh_gold_matviews(conn, logger) -> bool:
    """Refreshes the BI matviews (gold.mv_*) once, after the last per-ISIN commit (FND-0077).
    Non-fatal by design: a stale BI layer must not fail a P2 run whose metrics are already
    committed. Returns True on success. No-op (False) on SQLite, which has no matviews."""
    if not is_postgres_connection(conn):
        return False
    try:
        conn.execute("SELECT control.refresh_gold_matviews()")
        conn.commit()
        logger.info("[MATVIEW] gold.mv_* refreshed")
        return True
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.warning(f"[MATVIEW] refresh_gold_matviews failed (non-fatal): {exc}")
        return False


def run(
    isins: list[str] | None = None,
    horizons_filter: list[str] | None = None,
    metrics_filter: list[str] | None = None,    # v27 — metric families
    from_date: str | None = None,               # v27 — NAV start clip
    to_date: str | None = None,                 # v27 — NAV end clip
    force: bool = False,                        # v27 — bypass hash cache
    dry_run: bool = False,
    resume: bool = False,
    max_new_per_run: int = 0,                   # §4d — cap cold-start (0 = unlimited)
    backend: str | None = None,                 # migration addendum 2026-09-20 — None resolves
                                                  # FONDOS_DB_BACKEND (default "sqlite"); explicit
                                                  # "sqlite"/"postgres" overrides it for this run
                                                  # only, without touching the global switch.
) -> int:
    """
    Ejecuta el pipeline P2 completo o parcial.

    Todos los parámetros son opcionales; sin ellos procesa 3219 fondos completo.
    Consulta la docstring del módulo para descripción de cada parámetro.
    """
    # ---- Setup ------------------------------------------------
    global _ABORT, RUN_BATCH_ID
    _ABORT = False
    RUN_BATCH_ID = _new_batch_id()   # v26: unique id for this run; written to every Gold row
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
            n_warnings += 1
            logger.warning(
                f"[PIPELINE] Familias de metricas desconocidas ignoradas: {unknown}"
            )
            metrics_filter = [f for f in metrics_filter if f in _ALL_METRIC_FAMILIES]

    def _want(family: str) -> bool:
        """True si la familia debe calcularse según metrics_filter."""
        return metrics_filter is None or family in metrics_filter

    # Backfill flag (defined before try so finally can reference it)
    _is_backfill    = False
    _backfill_reason = ""

    # Counters (defined before try so finally can always reference them)
    t_run_start   = time.time()
    status        = "OK"
    n_processed   = 0
    n_skipped     = 0
    n_errors      = 0
    n_warnings    = 0   # P2-12: logger.warning() call count for RUN_SUMMARY
    total_written = 0
    total         = 0
    conn          = None

    try:
        logger.info(
            f"[RUN START] run_id={run_id} dry_run={dry_run} "
            f"metrics_filter={metrics_filter} from_date={from_date} "
            f"to_date={to_date} force={force}"
        )

        conn = get_connection(backend=backend)

        # EFF-1: add OLS cadence columns if not yet present (idempotent).
        # Postgres: db/pg/35_control.sql already defines them; even a no-op
        # ALTER ... IF NOT EXISTS requires table ownership, so issuing it at runtime
        # would fail under the least-privilege fondos_app role (FND-0072).
        if not is_postgres_connection(conn):
            for _col, _ctype in [("last_ols_quarter", "TEXT"), ("last_ols_nav_count", "INTEGER")]:
                try:
                    conn.execute(f"ALTER TABLE fund_metric_state ADD COLUMN {_col} {_ctype}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # column already exists

        # ── v26: Backfill detection ──────────────────────────────────────────
        # A run is a "backfill" when it forces recomputation of previously
        # calculated rows, either explicitly (--force) or because the algorithm
        # version changed (CALC_VERSION bump). Backfill runs are logged with
        # BACKFILL_START / BACKFILL_END markers in p2_pipeline_log for auditability.
        if force:
            _is_backfill = True
            _backfill_reason = f"--force flag | CALC_VERSION={CALC_VERSION}"
        else:
            try:
                _stored_algo = conn.execute(
                    "SELECT algorithm_version FROM fund_metrics "
                    "WHERE algorithm_version IS NOT NULL LIMIT 1"
                ).fetchone()
                if _stored_algo and _stored_algo[0] and _stored_algo[0] != CALC_VERSION:
                    _is_backfill = True
                    _backfill_reason = (
                        f"CALC_VERSION drift: stored={_stored_algo[0]} "
                        f"current={CALC_VERSION}"
                    )
            except Exception:
                # column may not exist yet on first run after migration. Roll back: on Postgres a
                # failed statement aborts the transaction, and every later statement would fail
                # with InFailedSqlTransaction (2026-09-26 transaction-poisoning audit).
                conn.rollback()

        if _is_backfill and not dry_run:
            try:
                ph = "%s" if is_postgres_connection(conn) else "?"
                conn.execute(
                    "INSERT INTO p2_pipeline_log"
                    " (isin, step, status, horizon, metric_version, message, batch_id)"
                    f" VALUES (NULL, 'BACKFILL_START', 'INFO', NULL, {ph}, {ph}, {ph})",
                    (METRIC_VERSION, _backfill_reason, RUN_BATCH_ID),
                )
                conn.commit()
            except Exception:
                conn.rollback()  # never block the pipeline on audit logging, nor leave the txn aborted
            logger.info(
                f"[BACKFILL] Full recompute initiated | batch_id={RUN_BATCH_ID} | "
                f"reason: {_backfill_reason}"
            )
        # ────────────────────────────────────────────────────────────────────

        # -- Cargar IPC --------------------------------------------
        if not ipc_available(conn, REGION_IPC):
            n_warnings += 1
            logger.warning(
                f"[IPC] No hay datos para '{REGION_IPC}' en series_inflation. "
                "Solo metricas nominales. Carga IPC con macro_discovery antes de P2."
            )
            ipc_df = None
        else:
            ipc_df = load_ipc(conn, REGION_IPC)
            ok, err = validate_ipc(ipc_df)
            if not ok:
                n_warnings += 1
                logger.warning(f"[IPC] Invalido ({err}) — solo metricas nominales")
                ipc_df = None

        # -- Cargar serie RF histórica (§4g — tipo depósito BCE, proxy €STR) ----
        rf_rate_df = load_rf_rate(conn)
        if rf_rate_df.empty:
            n_warnings += 1
            logger.warning(
                "[RF] Sin serie rate_deposit/EU en series_macro — "
                f"Sharpe/Sortino rolling usarán RISK_FREE_RATE_ANN plana ({RISK_FREE_RATE_ANN:.1%}). "
                "Carga datos BCE con macro_discovery antes de P2."
            )
        else:
            logger.info(
                f"[RF] Serie rate_deposit/EU cargada: {len(rf_rate_df)} meses "
                f"({rf_rate_df['date'].min().date()} → {rf_rate_df['date'].max().date()})"
            )

        # -- Cargar factores macro (una vez para todos los fondos) --
        macro_df = load_macro_factors(conn)
        if macro_df.empty:
            n_warnings += 1
            logger.warning(
                "[MacroFactors] Sin factores macro. "
                "Ejecuta macro_discovery antes de P2."
            )
        else:
            excluded = [c for c in ["spread_ig"] if c not in macro_df.columns]
            if excluded:
                logger.warning(
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
                    n_warnings += 1
                    logger.warning(
                        "  [M2_Global] No se pudo construir (datos insuficientes)"
                    )

        # -- Cargar historico de regimenes --
        regime_df = load_regime_history(conn)
        if regime_df.empty:
            n_warnings += 1
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
        _isins_explicit = isins is not None  # P2-04: bypass preflight on targeted runs
        if isins is None:
            # P2-01 fix: get_isins_with_nav filters to ISINs present in
            # fund_master (INNER JOIN). Compare against the raw count to
            # surface any future mismatch early.
            _nav_all = conn.execute(
                "SELECT COUNT(DISTINCT ISIN) FROM fund_nav_monthly"
            ).fetchone()[0]
            isins = get_isins_with_nav(conn)
            _nav_master = len(isins)
            if _nav_master < _nav_all:
                n_warnings += 1
                logger.warning(
                    f"[P2-01] {_nav_all - _nav_master} ISINs en fund_nav_monthly "
                    f"sin entrada en fund_master (excluidos del universo P2). "
                    f"Ejecuta P1 para clasificar esos fondos."
                )
            else:
                logger.info(
                    f"[P2-01] ISIN match OK: {_nav_master}/{_nav_all} ISINs "
                    f"de fund_nav_monthly presentes en fund_master."
                )

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

        # ── P2-04 Preflight: abort early when no new NAV ──────────────────────
        if not force and not _isins_explicit:
            _pf_new, _pf_never, _pf_total = count_isins_with_new_nav(
                conn, METRIC_VERSION
            )
            _pf_forced = len(force_recalc_isins)

            # Also gate on IPC freshness: new IPC rows change every fund's
            # input hash even when NAV is unchanged.  A single global check
            # suffices because IPC is a shared time series.
            _ph_pf = "%s" if is_postgres_connection(conn) else "?"
            _pf_last_calc = conn.execute(
                f"SELECT MAX(calculated_at) FROM fund_metric_state "
                f"WHERE metric_version={_ph_pf}",
                (METRIC_VERSION,),
            ).fetchone()[0]
            _pf_ipc_max = conn.execute(
                "SELECT MAX(date) FROM series_inflation WHERE geography='ES'"
            ).fetchone()[0]
            _pf_ipc_newer = bool(
                _pf_ipc_max and _pf_last_calc and _pf_ipc_max > _pf_last_calc
            )

            _pf_need = _pf_new + _pf_never + _pf_forced
            logger.info(
                f"[P2-04] Preflight: {_pf_total} ISINs universo | "
                f"{_pf_never} sin calculo previo | "
                f"{_pf_new} con NAV nuevo | "
                f"{_pf_forced} forzados (RECALCULATE_METRICS) | "
                f"IPC {'ACTUALIZADO' if _pf_ipc_newer else 'sin cambios'}"
            )

            if _pf_need == 0 and not _pf_ipc_newer:
                logger.info(
                    "[P2-04] ABORT: Sin datos nuevos desde el ultimo calculo. "
                    "Usa --force para recalcular igualmente."
                )
                _log(
                    conn, None, "PREFLIGHT", "ABORT_NO_NEW_NAV", None,
                    f"0/{_pf_total} ISINs con datos nuevos — todos up-to-date",
                    dry_run,
                )
                return 0  # P2-12: clean abort → rc=0
        # ──────────────────────────────────────────────────────────────────────

        # Legacy --resume (today-based skip)
        if resume and not dry_run:
            today_str = date.today().isoformat()
            _ph_resume = "%s" if is_postgres_connection(conn) else "?"
            done = {r[0] for r in conn.execute(
                f"SELECT DISTINCT isin FROM fund_metrics "
                f"WHERE calculation_date = {_ph_resume} AND metric_version = {_ph_resume}",
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

        # §4d: throttle new-fund cold-starts to avoid runtime spikes on large intakes.
        # New funds = ISINs with no fund_metric_state row → full OLS + series backfill.
        # Deferred ISINs are NOT dropped — they have no state row and will be picked up
        # automatically on the next run.
        if max_new_per_run > 0 and not force:
            import random as _random
            _existing_state = {r[0] for r in conn.execute(
                "SELECT isin FROM fund_metric_state"
            ).fetchall()}
            _new_isins = [i for i in isins if i not in _existing_state]
            if len(_new_isins) > max_new_per_run:
                _deferred = set(_random.sample(_new_isins, len(_new_isins) - max_new_per_run))
                isins = [i for i in isins if i not in _deferred]
                logger.info(
                    f"[P2-THROTTLE] §4d — {len(_new_isins)} fondos nuevos sin estado previo; "
                    f"procesando {max_new_per_run}, diferidos {len(_deferred)} fondos "
                    f"para la siguiente ejecucion (sin fund_metric_state → reprocesados automaticamente)."
                )

        total = len(isins)
        # EFF-1: OLS quarter identifier (YYYY-Q) — stable for the whole run
        current_quarter = f"{date.today().year}-{(date.today().month - 1) // 3 + 1}"
        # In-memory collection for cross-sectional rolling snapshot (avoids 2.5h SQL
        # on 16.6M-row fund_metric_timeseries; fund→{(metric,window,flag):(date,val)}).
        _latest_roll: dict[str, dict[tuple, tuple]] = {}
        logger.info(
            f"Procesando {total} fondos | dry_run={dry_run} | "
            f"OLS cadencia trimestral Q{current_quarter} | "
            f"IPC={'SI' if ipc_df is not None else 'NO'}"
        )

        # ================================================================
        # Per-fund loop
        # ================================================================
        for idx, isin in enumerate(isins, 1):

            # Graceful-abort (SIGINT / SIGBREAK)
            if _ABORT:
                status = "ABORTED"
                n_warnings += 1
                logger.warning(
                    f"[PIPELINE] Abort solicitado — "
                    f"deteniendo tras {n_processed} fondos procesados."
                )
                break

            t_fund = time.time()
            logger.info(
                "", extra=dict(
                    p2_idx=idx, p2_total=total, p2_isin=isin,
                    p2_evt="START", p2_detail="", p2_count="", p2_dur_ms=0,
                )
            )

            _fund_txn_open = False   # EFF-2: guard for per-fund txn rollback in except
            try:
                # ---- NAV load + validation ---------------------------
                nav_df = load_nav(conn, isin)
                if nav_df.empty:
                    logger.debug(
                        "", extra=dict(
                            p2_idx=idx, p2_total=total, p2_isin=isin,
                            p2_evt="SKIP", p2_detail="sin NAV",
                            p2_count="", p2_dur_ms=round((time.time() - t_fund) * 1000),
                        )
                    )
                    _log(conn, isin, "NAV_LOAD", "SKIP", None, "Sin datos NAV", dry_run)
                    n_skipped += 1
                    continue

                ok, err = validate_nav(nav_df)
                if not ok:
                    n_warnings += 1
                    logger.warning(
                        "", extra=dict(
                            p2_idx=idx, p2_total=total, p2_isin=isin,
                            p2_evt="SKIP", p2_detail=f"NAV invalido: {err}",
                            p2_count="", p2_dur_ms=round((time.time() - t_fund) * 1000),
                        )
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
                        elapsed_ms = round((time.time() - t_fund) * 1000)
                        logger.debug(
                            "", extra=dict(
                                p2_idx=idx, p2_total=total, p2_isin=isin,
                                p2_evt="Cache hit", p2_detail=f"hash={current_hash[:8]}",
                                p2_count="", p2_dur_ms=elapsed_ms,
                            )
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

                # EFF-2: single transaction for ALL per-fund writes.
                # All write helpers (_write_metrics, _write_timeseries,
                # _replace_beta_set, _log, _upsert_metric_state) check
                # conn.in_transaction and skip their own BEGIN/COMMIT when True.
                # This collapses ~40 lock-acquire/WAL-frame cycles per fund into 1.
                # Reads inside the transaction (load_nav_daily, benchmark lookups,
                # fund_master SELECT) are safe: WAL gives a consistent snapshot.
                _fund_txn_open = False
                if not dry_run:
                    begin_immediate(conn)
                    _fund_txn_open = True

                # ---- Since inception ---------------------------------
                if _want("risk"):
                    if horizons_filter is None or "since_inception" in horizons_filter:
                        isin_written += _process_horizon(
                            isin, nav_df, ipc_df, "since_inception", conn, dry_run,
                            rf_rate_df=rf_rate_df,
                        )

                # ---- Ventanas de crisis ------------------------------
                if _want("risk"):
                    for crisis_name, (start, end) in CRISIS_WINDOWS.items():
                        if horizons_filter and crisis_name not in horizons_filter:
                            continue
                        nav_w = slice_window(nav_df, start, end)
                        if len(nav_w) < MIN_NAV_ROWS:
                            continue
                        # ipc_df pasado SIN recortar (root-cause fix 2026-09-18):
                        # deflate_nav() usa merge_asof(direction='backward'), que
                        # necesita poder buscar hacia atras del inicio de la
                        # ventana para resolver correctamente su propio borde --
                        # recortar ipc_df aqui le quitaba esa capacidad y
                        # corrompia el real_flag=1 de cada ventana. Ver
                        # deflation.py::deflate_nav docstring para el detalle.
                        isin_written += _process_horizon(
                            isin, nav_w, ipc_df, crisis_name, conn, dry_run,
                            rf_rate_df=rf_rate_df,
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
                        if len(nav_w) < MIN_NAV_ROWS:
                            continue
                        # ipc_df pasado SIN recortar -- ver comentario identico
                        # arriba (ventanas de crisis), misma causa raiz.
                        isin_written += _process_horizon(
                            isin, nav_w, ipc_df, horizon_name, conn, dry_run,
                            rf_rate_df=rf_rate_df,
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
                            sh_rows = _rows_from_metric_tuples(sh_list, len(nav_sh))
                            isin_written += _write_metrics(
                                conn, isin, sh_rows, sh_name, dry_run,
                                algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID,
                                metric_version=METRIC_VERSION_SHORT,
                            )
                            if sh_rows:
                                _log(conn, isin, "CALC", "OK", sh_name,
                                     f"{len(sh_rows)} metricas cortas (d1)", dry_run)

                # ---- Atributos del fondo ----------------------------
                _ph_fm = "%s" if is_postgres_connection(conn) else "?"
                _fm = conn.execute(
                    f"""SELECT Fund_Nature, Fund_Currency, Hedging_Policy,
                              Asset_Currency, Geography, Development_Status
                       FROM fund_master WHERE ISIN={_ph_fm}""", (isin,)
                ).fetchone()
                fund_nature        = _fm[0] if _fm else None
                fund_currency      = _fm[1] if _fm else None
                hedging_policy     = _fm[2] if _fm else None
                asset_currency     = _fm[3] if _fm else None
                geography          = _fm[4] if _fm else None
                development_status = _fm[5] if _fm else None

                # ---- Familias que requieren macro data availability --
                _has_macro = not macro_df.empty and len(nav_df) >= MIN_NAV_MACRO
                _ols_ran   = False   # EFF-1: tracks whether OLS was computed this fund

                if _has_macro:
                    # -- Sensibilidad macro (OLS quarterly cadence, EFF-1) --
                    if _want("macro"):
                        _skip_ols = _ols_is_fresh(conn, isin, len(nav_df), current_quarter, force=force)
                        if _skip_ols:
                            logger.debug(
                                "", extra=dict(
                                    p2_idx=idx, p2_total=total, p2_isin=isin,
                                    p2_evt="OLS skip",
                                    p2_detail=f"fresh Q{current_quarter}",
                                    p2_count=len(nav_df),
                                    p2_dur_ms=round((time.time() - t_fund) * 1000),
                                )
                            )
                        else:
                            sens_list = compute_macro_sensitivity(
                                nav_df, macro_df,
                                geography=geography,
                                development_status=development_status,
                            )
                            sens_rows = _rows_from_metric_tuples(sens_list, len(nav_df))
                            # Use _replace_beta_set (delete+insert, one transaction)
                            # so VIF-dropped factors from prior runs are cleaned up.
                            # Non-beta derived metrics (energy_sensitivity_pct,
                            # hy_spread_sensitivity_pct, macro_r2) are also in
                            # sens_rows and are handled by the INSERT OR REPLACE
                            # inside the helper — orphan risk there is negligible
                            # since they are always recomputed when OLS runs.
                            isin_written += _replace_beta_set(
                                conn, isin, sens_rows, "since_inception", dry_run,
                                algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID,
                                metric_version=METRIC_VERSION,
                            )
                            _ols_ran = True

                # -- Momentum / Capture / Persistencia / FX / Regimen --
                # Registry-driven (P2 canonicalización, 2026-09-14): these 5
                # families share the exact "gate + compute + _write_metrics"
                # shape (previously a copy-pasted block each). See
                # MetricFamilySpec / _PER_FUND_METRIC_FAMILIES above for the
                # gate definitions this loop evaluates, and the docstring
                # there for which families are deliberately NOT registry-driven.
                _fund_ctx = _FundMetricCtx(
                    isin=isin, conn=conn, nav_df=nav_df,
                    fund_nature=fund_nature, fund_currency=fund_currency,
                    hedging_policy=hedging_policy, asset_currency=asset_currency,
                    regime_df=regime_df, has_macro=_has_macro,
                )
                for _spec in _PER_FUND_METRIC_FAMILIES:
                    isin_written += _run_metric_family(_spec, _fund_ctx, dry_run, _want)

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
                        risk_free_rate=RISK_FREE_RATE_ANN,         # fallback scalar
                        rf_series=rf_rate_df if not rf_rate_df.empty else None,  # §4g
                    )
                    ts_written = _write_timeseries(
                        conn, roll_rows, dry_run,
                        algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID,
                    )
                    if ts_written:
                        _log(conn, isin, "ROLLING", "OK", "all_windows",
                             f"{ts_written} filas timeseries rolling", dry_run)

                    # Collect latest (date, value) per (metric, window, real_flag) for
                    # post-loop cross-sectional snapshot — avoids the 2.5h SQL query.
                    if roll_rows:
                        fund_latest: dict = {}
                        for _r in roll_rows:
                            _k = (_r["metric"], _r["window"], int(_r["real_flag"]))
                            _ex = fund_latest.get(_k)
                            if _ex is None or _r["date"] > _ex[0]:
                                fund_latest[_k] = (_r["date"], _r["value"])
                        if fund_latest:
                            _latest_roll[isin] = fund_latest

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
                                conn, isin, w_rows, w_name, dry_run,
                                algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID,
                                metric_version=METRIC_VERSION,
                            )

                # v25: consumir flag RECALCULATE_METRICS (inside per-fund txn)
                if isin_written > 0 and not dry_run:
                    ph = "%s" if is_postgres_connection(conn) else "?"
                    conn.execute(
                        "UPDATE nav_sources SET data_status='OK' "
                        f"WHERE isin={ph} AND data_status='RECALCULATE_METRICS'",
                        (isin,)
                    )

                # v27: upsert input-hash para skip en proximas ejecuciones
                if isin_written > 0:
                    _upsert_metric_state(conn, isin, current_hash, dry_run)
                    # EFF-1: persist OLS quarter so next run in same quarter skips
                    if _ols_ran:
                        _update_ols_state(conn, isin, current_quarter, len(nav_df), dry_run)

                # EFF-2: commit the single per-fund transaction
                if _fund_txn_open:
                    conn.execute("COMMIT")
                    _fund_txn_open = False

                total_written += isin_written
                elapsed_ms = round((time.time() - t_fund) * 1000)
                logger.info(
                    "", extra=dict(
                        p2_idx=idx, p2_total=total, p2_isin=isin,
                        p2_evt="END", p2_detail="",
                        p2_count=isin_written, p2_dur_ms=elapsed_ms,
                    )
                )

                n_processed += 1

            except Exception as exc:
                # EFF-2: rollback the per-fund transaction on any error
                if _fund_txn_open:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    _fund_txn_open = False
                n_errors += 1
                elapsed_ms = round((time.time() - t_fund) * 1000)
                logger.error(
                    traceback.format_exc(), extra=dict(
                        p2_idx=idx, p2_total=total, p2_isin=isin,
                        p2_evt="FUND-ERR", p2_detail=str(exc),
                        p2_count="", p2_dur_ms=elapsed_ms,
                    )
                )
                # Never abort the batch for one fund's error
                continue

        # -- Alarm engine (cross-seccional, post-loop) -----------------
        # v28 memory fix: read ONLY the latest date per (metric,window,real_flag)
        # instead of the full fund_metric_timeseries table (~14 M rows / 5 GB).
        # pctile_self is now computed per-fund in the loop above;
        # this block only needs to produce pctile_cat, zscore_cat, and alerts.
        # Guard: skip on --dry-run (diagnostic runs must not load the cross-fund data).
        # P2-05: skip cross-sectional snapshot when nothing was recomputed this run.
        # _latest_roll is empty → fallback DB query (16.6M-row scan) would fire
        # for no benefit; the previous run's snapshot is still correct.
        if _want("rolling") and ROLLING_STATS_ENABLED and not dry_run and n_processed > 0:
            logger.info(
                "[ROLLING] Calculando snapshot cross-seccional (ultima fecha)..."
            )
            try:
                # Build latest_df for cross-sectional category snapshot.
                # PRIMARY PATH: in-memory collection from per-fund loop (_latest_roll).
                #   Populated for all funds processed in THIS run (not hash-skipped).
                #   Avoids the 2.5h SQL self-join on 16.6M rows.
                # FALLBACK PATH: DB query when most funds were hash-skipped (monthly
                #   incremental runs); DB query is slow but necessary for full coverage.
                if len(_latest_roll) >= max(50, total // 10):
                    logger.info(
                        f"[ROLLING] Usando coleccion en memoria: "
                        f"{len(_latest_roll)} fondos procesados"
                    )
                    _fn_map = {r[0]: r[1] for r in conn.execute(
                        "SELECT ISIN, Fund_Nature FROM fund_master"
                    ).fetchall()}
                    _recs = []
                    for _isin_k, _mv in _latest_roll.items():
                        _fn = _fn_map.get(_isin_k)
                        for (_m, _w, _f), (_dv, _val) in _mv.items():
                            _recs.append({
                                "isin": _isin_k, "metric": _m, "window": _w,
                                "date": _dv, "value": _val, "real_flag": _f,
                                "Fund_Nature": _fn,
                            })
                    latest_df = pd.DataFrame(_recs) if _recs else pd.DataFrame()
                else:
                    # Fallback: DB query (slow on large timeseries; correct for
                    # hash-skip-dominated runs). compute_category_snapshot handles
                    # per-fund latest internally after BUG-ROLL-LATEST-B fix.
                    logger.info(
                        f"[ROLLING] Fallback DB query (solo {len(_latest_roll)} "
                        "fondos en memoria — mayoría hash-skipped)"
                    )
                    # window -> window_label: real column rename on Postgres (reserved word,
                    # db/pg/rename_map.yaml). fetchall()+manual DataFrame instead of pd.read_sql(sql,
                    # conn) — same reasoning as db_readers.py::load_fund_attributes: works against a
                    # raw psycopg3 connection but emits a UserWarning every call, avoided elsewhere
                    # in this migration. This is the exact self-join the migration plan calls out as
                    # the reason gold.mv_fmts_peer_stats/mv_fmts_latest exist (2.5h against the base
                    # tables on the full 32M-row table) — left querying the base tables here
                    # deliberately, since this fallback path only ever runs on a small in-memory
                    # subset (len(_latest_roll) < max(50, total//10)); redirecting it to the
                    # matviews is a Stage 9/cutover-time performance decision, not a correctness one.
                    _window_col = "window_label" if is_postgres_connection(conn) else "window"
                    _latest_cols = ["isin", "metric", "window", "date", "value", "real_flag",
                                    "Fund_Nature"]
                    _latest_rows = conn.execute(f"""
                        SELECT t.isin, t.metric, t.{_window_col} AS window, t.date,
                               t.value, t.real_flag, m.Fund_Nature
                        FROM fund_metric_timeseries t
                        JOIN (
                            SELECT isin, metric, {_window_col}, real_flag, MAX(date) AS mx
                            FROM fund_metric_timeseries
                            WHERE metric IN (
                                'vol_ann','max_dd','return_ann'
                            )
                            GROUP BY isin, metric, {_window_col}, real_flag
                        ) latest
                          ON  t.isin       = latest.isin
                          AND t.metric     = latest.metric
                          AND t.{_window_col} = latest.{_window_col}
                          AND t.real_flag  = latest.real_flag
                          AND t.date       = latest.mx
                        LEFT JOIN fund_master m ON t.isin = m.ISIN
                        WHERE t.metric IN (
                            'vol_ann','max_dd','return_ann'
                        )
                    """).fetchall()
                    latest_df = pd.DataFrame(_latest_rows, columns=_latest_cols)
                if not latest_df.empty:
                    # compute_category_snapshot expects 'date' column —
                    # latest_df already has it (the latest date only).
                    cat_df = compute_category_snapshot(latest_df, min_peers=MIN_PEERS)

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
                            conn, s_isin, s_rows, s_window, dry_run,
                            algorithm_version=CALC_VERSION, batch_id=RUN_BATCH_ID,
                            metric_version=METRIC_VERSION,
                        )
                    logger.info(
                        f"[ROLLING] {len(cat_sig_rows)} señales de categoria → "
                        f"{n_cat} escritas en fund_metrics"
                    )

                    al_rows  = compute_alerts(cat_df, ALERT_RULES, min_peers=MIN_PEERS)
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
                n_warnings += 1
                logger.warning(
                    f"[ROLLING] Motor rolling falló (no fatal): {exc}\n"
                    f"{traceback.format_exc()}"
                )
                # Non-fatal, so the run continues on this connection (matview refresh, RUN_SUMMARY):
                # clear a Postgres-aborted transaction first (transaction-poisoning audit).
                conn.rollback()

        # BI layer: refresh once, after every per-ISIN commit and the rolling engine.
        if not dry_run and total_written > 0 and not _refresh_gold_matviews(conn, logger) \
                and is_postgres_connection(conn):
            n_warnings += 1

    except Exception as exc:
        status = "ERROR"
        logger.critical(
            f"[PIPELINE] Error fatal no controlado: {exc}\n"
            f"{traceback.format_exc()}"
        )
        # P2-12: swallow here so finally runs and rc=2 is returned to __main__
        # The finally block writes RUN_SUMMARY on this same connection: clear an aborted
        # Postgres transaction first or the ERROR summary would silently fail to persist.
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass  # connection already unusable; the finally block guards its own writes

    finally:
        elapsed_total = time.time() - t_run_start
        logger.info(
            f"[RUN END] run_id={run_id} status={status} "
            f"processed={n_processed} skipped={n_skipped} errors={n_errors} "
            f"warnings={n_warnings} total_written={total_written} "
            f"elapsed={elapsed_total:.0f}s"
        )
        sys.stdout.flush()
        sys.stderr.flush()
        # P2-12: persist RUN_SUMMARY row for operational observability
        if conn is not None and not dry_run:
            try:
                _ph = "%s" if is_postgres_connection(conn) else "?"
                begin_immediate(conn)
                conn.execute(
                    "INSERT INTO p2_pipeline_log"
                    " (isin, step, status, horizon, metric_version, message, batch_id)"
                    f" VALUES (NULL, 'RUN_SUMMARY', {_ph}, NULL, {_ph}, {_ph}, {_ph})",
                    (
                        status,
                        METRIC_VERSION,
                        (
                            f"run_id={run_id} processed={n_processed} "
                            f"skipped={n_skipped} errors={n_errors} "
                            f"warnings={n_warnings} written={total_written} "
                            f"elapsed={elapsed_total:.0f}s"
                        ),
                        RUN_BATCH_ID,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.rollback()  # never crash in finally; leave no aborted txn for what follows

            # v26: BACKFILL_END marker (logged after RUN_SUMMARY so it's always last)
            if _is_backfill:
                try:
                    _ph = "%s" if is_postgres_connection(conn) else "?"
                    begin_immediate(conn)
                    conn.execute(
                        "INSERT INTO p2_pipeline_log"
                        " (isin, step, status, horizon, metric_version, message, batch_id)"
                        f" VALUES (NULL, 'BACKFILL_END', {_ph}, NULL, {_ph}, {_ph}, {_ph})",
                        (
                            status,
                            METRIC_VERSION,
                            (
                                f"batch_id={RUN_BATCH_ID} recomputed={n_processed} "
                                f"errors={n_errors} elapsed={elapsed_total:.0f}s"
                            ),
                            RUN_BATCH_ID,
                        ),
                    )
                    conn.execute("COMMIT")
                except Exception:
                    conn.rollback()  # never crash in finally; leave no aborted txn for what follows

        # ── Observability signals ─────────────────────────────────────────────
        # Three diagnostic lines emitted after every non-dry run that processed
        # at least one fund. Each is independently try/except-guarded so one
        # failing query never silences the others or crashes the pipeline.
        # Canonical documentation for interpretation lives in the two audit
        # skills (pipelineP2Audit §3 Step 2, pipelineP1P2Audit §3 Step 5).
        if conn is not None and not dry_run and n_processed > 0:
            _obs_today = date.today().isoformat()
            # run_start_iso: calendar day when run() was invoked.  Overnight P2
            # runs begin ~21:00 and finish ~02:40, crossing midnight.  fund_metric_state
            # is stamped with date.today() at write time, so two consecutive calendar
            # days appear.  Using run_start_iso as a lower bound ensures all funds
            # touched by this run are counted — not just those stamped on _obs_today.
            _run_start_iso = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"

            # [RUN COHORT] load_ts spread — two-timestamp model (see module docstring).
            # EXPECTED: stale rows are beta_* / energy_sensitivity_pct /
            # hy_spread_sensitivity_pct (EFF-1 cadence). Everything else is ANOMALY.
            try:
                cohort_rows = load_ts_cohort(conn, METRIC_VERSION, _run_start_iso)
                cohort_str = ", ".join(
                    f"{r[0]}: {r[1]}" for r in cohort_rows
                ) if cohort_rows else "none"
                logger.info(
                    f"[RUN COHORT] load_ts spread for funds processed this run "
                    f"({METRIC_VERSION}, run_start={_run_start_iso}): {cohort_str}"
                )
            except Exception:
                pass  # never crash in finally

            # [NAV STALE] funds recomputed on prices > 60d old.
            # Stale prices yield inaccurate vol/sharpe/drawdown even though the
            # fingerprint hash matched NAV row count. Cause: nav_discovery skipped.
            try:
                n_stale = count_stale_nav_funds(
                    conn, METRIC_VERSION, _run_start_iso,
                    max_age_days=60, as_of_iso=_obs_today,
                )
                if n_stale > 0:
                    logger.warning(
                        f"[NAV STALE] {n_stale} fund(s) recomputed on NAV older than 60d "
                        "— run nav_discovery --mode update, then P2 with --force"
                    )
                else:
                    logger.info(
                        "[NAV STALE] All recomputed funds have fresh NAV (<= 60d old)"
                    )
            except Exception:
                pass  # never crash in finally

            # [COVERAGE] distinct non-NULL ISIN count per P3-consumed metric.
            # Diff this line run-over-run from the log. A drop > ~2% is a regression
            # signal (upstream NAV loss or calc failure) and must be triaged.
            try:
                cov = coverage_snapshot(conn, list(_P3_CONSUMED_METRICS))
                cov_str = "  ".join(f"{m}={n}" for m, n in cov)
                logger.info(f"[COVERAGE] {cov_str}")
            except Exception:
                pass  # never crash in finally

        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _allow_sleep()

    # P2-12: deterministic exit codes — 0=OK/ABORT, 1=fund errors, 2=fatal
    return 2 if status == "ERROR" else (1 if n_errors > 0 else 0)


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
    parser.add_argument(
        "--max-new-per-run", type=int, default=0, dest="max_new_per_run",
        help="§4d — cap de fondos nuevos (sin fund_metric_state) por ejecucion. "
             "0 = sin limite (default). Ej: --max-new-per-run 100 distribuye "
             "un intake masivo en multiples ejecuciones sin comprometer el SLA."
    )
    parser.add_argument(
        "--backend", choices=["sqlite", "postgres"], default=None,
        help="Backend de BD para esta ejecucion (migracion, addendum 2026-09-20). "
             "Si se omite, resuelve la variable de entorno FONDOS_DB_BACKEND "
             "('sqlite' si no esta definida)."
    )
    args = parser.parse_args()

    from shared.backlog_client import capture_exceptions
    with capture_exceptions(object_name="run_pipeline.py", object_type="JOB"):
        _rc = run(  # P2-12: propagate exit code (0=OK, 1=fund errors, 2=fatal)
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
            max_new_per_run=args.max_new_per_run,
            backend=args.backend,
        )
    sys.exit(_rc)
