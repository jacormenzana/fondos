# proyecto2/src/utils/logger.py
# -*- coding: utf-8 -*-
"""
Logger de pipeline P2 con timestamps ISO8601 y salida a stdout (v27).

Uso:
    from src.utils.logger import get_pipeline_logger
    logger = get_pipeline_logger(run_id="20260727_224410")
    logger.info("Mensaje")

Formato de salida:
    YYYY-MM-DD HH:MM:SS | LEVEL   | [run=<run_id>] mensaje

Diseño:
  - StreamHandler → stdout (line-buffered; compatible con >> del .bat)
  - propagate=False para evitar duplicados con el root logger
  - Reutiliza el logger si ya existe (no añade handlers duplicados)
"""

import logging
import sys


class _StructuredFmt(logging.Formatter):
    """
    Canonical P2 structured log line.

    Output: [idx/total] | YYYY-MM-DD HH:MM:SS | run_id | ISIN | LEVEL | EventType | Detail | Count | Duration(ms)

    Per-ISIN callers pass structured data via extra=dict(p2_idx, p2_total, p2_isin,
    p2_evt, p2_detail, p2_count, p2_dur_ms).  Non-ISIN callers omit extra entirely;
    those fields become empty strings so the column count stays stable.
    """

    def __init__(self, run_id: str) -> None:
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S")
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        ts      = self.formatTime(record, self.datefmt)
        idx     = getattr(record, "p2_idx",    "")
        total   = getattr(record, "p2_total",  "")
        isin    = getattr(record, "p2_isin",   "")
        ev_type = getattr(record, "p2_evt",    record.getMessage())
        detail  = getattr(record, "p2_detail", "")
        count   = getattr(record, "p2_count",  "")
        dur_ms  = getattr(record, "p2_dur_ms", "")

        if idx != "" and total != "":
            w = max(len(str(total)), 4)
            idx_str = f"[{int(idx):{w}d}/{int(total):{w}d}]"
        else:
            idx_str = "[    /    ]"

        return (
            f"{idx_str} | {ts} | {self._run_id} | {isin} | "
            f"{record.levelname} | {ev_type} | {detail} | {count} | {dur_ms}"
        )


def get_pipeline_logger(run_id: str = "pipeline") -> logging.Logger:
    """
    Devuelve un Logger configurado para el pipeline P2.

    Si ya existe un logger con ese run_id (mismo nombre), lo devuelve
    sin añadir handlers duplicados — seguro llamarlo varias veces.

    Parameters
    ----------
    run_id : identificador único de la ejecución (p.ej. timestamp de inicio
             en formato YYYYMMDD_HHMMSS).
    """
    name   = f"p2.pipeline.{run_id}"
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(_StructuredFmt(run_id))
    sh.setLevel(logging.DEBUG)
    logger.addHandler(sh)
    logger.propagate = False
    return logger
