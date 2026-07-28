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
    fmt = logging.Formatter(
        f"%(asctime)s | %(levelname)-7s | [run={run_id}] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG)
    logger.addHandler(sh)
    logger.propagate = False
    return logger
