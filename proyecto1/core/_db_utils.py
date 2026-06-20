"""
core/_db_utils.py — Helpers de lectura efectiva de campos persistidos en BD.

Cambios:

  v1 (2026-04-25) — BL-49/50/53/56/57: helper _eff() centralizado.
                    Causa raíz arquitectónica común:
                    El patrón COALESCE en sqlite_writer preserva valores en BD
                    que el ciclo en curso no recalcula. Las reglas de inferencia
                    del pipeline operaban sobre fund_master_record (dict del
                    ciclo) sin leer la BD, perdiendo información para fondos
                    CACHED.
                    Solución (Principio #1 + #2 DRY): wrapper que devuelve
                    el valor efectivo (dict del ciclo > BD > None) con caché
                    por ISIN para evitar lecturas repetidas a BD.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


# Conjunto canónico de campos cuya lectura efectiva es requerida por reglas
# INTER del pipeline. Cualquier campo nuevo persistido vía COALESCE que
# participe en inferencias INTER debe añadirse aquí.
_EFF_FIELDS_WHITELIST = frozenset({
    # Geográficos / Universo
    "Geography", "Investment_Universe", "Investment_Focus",
    # Cobertura de divisa
    "Currency_Hedged", "Hedging_Policy", "Fund_Currency",
    # Sectorial / temático
    "Sector_Focus", "Theme",
    # Benchmark
    "Benchmark_Declared", "Benchmark_Type",
    # Clasificación principal
    "Fund_Nature", "Type", "Family", "Subtype",
    "Strategy", "Replication_Method",
    # Otros v17
    "Market_Cap_Focus", "Accumulation_Policy",
    "Credit_Quality", "Profile",
})


class EffectiveReader:
    """
    Lector de valor efectivo con caché por ISIN.

    Uso típico (al inicio del bucle por fondo en pipeline.py):

        eff = EffectiveReader(conn, isin)
        ...
        # En cualquier regla INTER:
        _geo = eff.get("Geography", fund_master_record)
        _univ = eff.get("Investment_Universe", fund_master_record)

    Semántica:
      - Si fund_master_record[campo] es no-None → devuelve ese valor.
      - Si es None → consulta BD (una sola vez por campo) y cachea.
      - Si BD también es None → devuelve None.

    Garantías:
      - Como mucho UNA query SELECT por campo y por ISIN (caché interna).
      - No reintroduce valores en fund_master_record (no muta el dict).
        Esto es deliberado: el COALESCE de sqlite_writer ya preserva el
        valor de BD; mutar el dict podría provocar dobles escrituras y
        romper la semántica "el bloque tiene la última palabra" para
        campos no-COALESCE.
    """

    __slots__ = ("_conn", "_isin", "_cache", "_bd_loaded")

    def __init__(self, conn: sqlite3.Connection, isin: str):
        self._conn = conn
        self._isin = isin
        self._cache: Dict[str, Optional[Any]] = {}
        self._bd_loaded: bool = False

    def _load_all_from_bd(self) -> None:
        """Carga TODOS los campos whitelist en una sola query (idempotente)."""
        if self._bd_loaded:
            return
        cols = ", ".join(sorted(_EFF_FIELDS_WHITELIST))
        try:
            row = self._conn.execute(
                f"SELECT {cols} FROM fund_master WHERE ISIN=?",
                (self._isin,)
            ).fetchone()
        except sqlite3.OperationalError:
            # Columna ausente en esquema — degradar a None silenciosamente.
            row = None
        if row:
            for i, col in enumerate(sorted(_EFF_FIELDS_WHITELIST)):
                self._cache[col] = row[i]
        else:
            for col in _EFF_FIELDS_WHITELIST:
                self._cache[col] = None
        self._bd_loaded = True

    def get(
        self,
        field: str,
        fund_master_record: Dict[str, Any],
    ) -> Optional[Any]:
        """
        Devuelve el valor efectivo del campo:
          1. Si fund_master_record[field] es no-None → retorna ese valor.
          2. Si es None → carga BD (lazy + cached) y retorna BD value.
          3. Si BD también es None → retorna None.

        Levanta KeyError si `field` no está en _EFF_FIELDS_WHITELIST
        (protección contra typos y campos no-INTER).
        """
        if field not in _EFF_FIELDS_WHITELIST:
            raise KeyError(
                f"_EFF_FIELDS_WHITELIST no contiene '{field}'. "
                "Si es un campo INTER legítimo, añádelo a la whitelist en "
                "core/_db_utils.py."
            )
        v = fund_master_record.get(field)
        if v is not None:
            return v
        if not self._bd_loaded:
            self._load_all_from_bd()
        return self._cache.get(field)

    def get_bd_only(self, field: str) -> Optional[Any]:
        """
        Variante que IGNORA el dict del ciclo y devuelve solo el valor en BD.
        Útil para diagnóstico o reglas que necesitan comparar dict vs BD.
        """
        if field not in _EFF_FIELDS_WHITELIST:
            raise KeyError(
                f"_EFF_FIELDS_WHITELIST no contiene '{field}'."
            )
        if not self._bd_loaded:
            self._load_all_from_bd()
        return self._cache.get(field)


def make_eff_reader(
    conn: sqlite3.Connection,
    isin: str,
) -> EffectiveReader:
    """Constructor convencional. Atajo para uso desde pipeline."""
    return EffectiveReader(conn, isin)
