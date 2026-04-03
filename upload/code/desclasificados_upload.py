# proyecto1/blocks/desclasificados.py
# -*- coding: utf-8 -*-
"""
Bloque DESCLASIFICADOS — canonico v2

Rol
---
Procesa fondos que en una ejecucion previa fueron clasificados por el bloque
Restantes con una Fund_Nature distinta de 'Restantes' (p.ej. 'Monetario',
'Renta Variable', etc.) pero que no recibieron la caracterizacion completa
del bloque primario correspondiente.

Relacion con Restantes
----------------------
    Restantes      → universo: Fund_Nature IS NULL (primera clasificacion)
    Desclasificados→ universo: Heuristic_Block='RESTANTES'
                               AND Fund_Nature NOT IN ('Restantes', NULL)

Logica de clasificacion
-----------------------
Identica a Restantes: detecta naturaleza con las 3 capas y delega al
bloque primario correspondiente.
La unica diferencia con Restantes es el universo de fondos que procesa.

Se reutiliza classify_fund de restantes directamente — fuente unica
de verdad para la clasificacion de fondos no heuristicos.
"""

from __future__ import annotations

from typing import Optional, Dict, List

# Reutilizar classify_fund de restantes: misma logica, distinto universo
from blocks.restantes import classify_fund  # noqa: F401  (re-exportar)


BLOCK_NAME        = "desclasificados"
# FUND_NATURE_VALUE no aplica a este bloque.
# La Fund_Nature se determina dinamicamente en classify_fund()
# segun las señales detectadas en cada fondo (KIID, nombre, SRRI).
# El pipeline NO usa esta constante — lee siempre classification.get("Fund_Nature"). en restantes.py


# =====================================================
# Universo del bloque
# =====================================================

def get_universe_isins(df_master, conn=None) -> List[str]:
    """
    Universo del bloque Desclasificados.

    Fondos que cumplen ambas condiciones:
      1. Heuristic_Block = 'RESTANTES'  (procesados previamente por restantes)
      2. Fund_Nature IS NOT NULL AND Fund_Nature != 'Restantes'
         (restantes les asigno una naturaleza concreta en una ejecucion previa)

    Estos fondos tienen Fund_Nature correcta pero caracterizacion incompleta
    porque el bloque restantes original aplicaba _classify_as_* simplificados.
    Este bloque les aplica la caracterizacion completa del bloque primario.
    """
    if conn is None:
        return []

    rows = conn.execute(
        """
        SELECT ISIN FROM fund_master
        WHERE Heuristic_Block = 'RESTANTES'
          AND Fund_Nature IS NOT NULL
          AND Fund_Nature != 'Restantes'
        ORDER BY Fund_Nature, ISIN
        """
    ).fetchall()
    return [r[0] for r in rows if r[0]]
