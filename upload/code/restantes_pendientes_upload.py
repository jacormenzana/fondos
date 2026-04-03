# proyecto1/blocks/restantes_pendientes.py
# -*- coding: utf-8 -*-
"""
Bloque RESTANTES_PENDIENTES — parche temporal

Proposito
---------
La ejecucion del bloque restantes se interrumpio a mitad.
Este bloque procesa SOLO los ISINs pendientes: los que estan en el Excel
pero NO aparecen en fund_master (ni como procesados por restantes ni por
ningún otro bloque).

Diferencia con restantes
------------------------
    restantes           → Excel - procesados_por_primarios
                          + Heuristic_Block='RESTANTES' (re-ejecucion)
    restantes_pendientes→ Excel - TODOS_los_ya_en_fund_master
                          (solo los que aún no existen en la BD)

Logica de clasificacion
-----------------------
Identica a restantes — reutiliza classify_fund directamente.

NOTA: este bloque es temporal. Una vez completado el procesamiento
se puede eliminar y usar restantes normalmente.
"""

from __future__ import annotations

from typing import Optional, Dict, List

from blocks.restantes import classify_fund  # noqa: F401  misma logica


BLOCK_NAME        = "restantes"  # debe ser "restantes" para que el pipeline pase conn
FUND_NATURE_VALUE = None  # dinamico — ver classify_fund() en restantes.py


def get_universe_isins(df_master, conn=None) -> List[str]:
    """
    ISINs del Excel que NO están en fund_master todavía.
    Son los fondos que la ejecucion interrumpida no llegó a procesar.
    """
    all_isins = set(df_master["ISIN"].dropna().astype(str).unique())

    if conn is None:
        return sorted(all_isins)

    # Todos los ISINs que ya están en fund_master (procesados por cualquier bloque)
    rows = conn.execute("SELECT ISIN FROM fund_master").fetchall()
    already_done = {r[0] for r in rows if r[0]}

    pending = all_isins - already_done
    return sorted(pending)
