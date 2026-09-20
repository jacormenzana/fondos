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

try:
    from shared.db import is_postgres_connection, table_columns
except ModuleNotFoundError:
    import sys as _sys_shared
    from pathlib import Path as _Path_shared
    _shared_root = _Path_shared(__file__).resolve().parents[2]
    if str(_shared_root) not in _sys_shared.path:
        _sys_shared.path.insert(0, str(_shared_root))
    from shared.db import is_postgres_connection, table_columns


# Conjunto canónico de campos cuya lectura efectiva es requerida por reglas
# INTER del pipeline. Cualquier campo nuevo persistido vía COALESCE que
# participe en inferencias INTER debe añadirse aquí.
# FIX-EFF-CH (2026-07-04): "Currency_Hedged", "Type" y "Subtype" corregidas
# en esta whitelist. Causa raíz CRÍTICA: las 3 referencian columnas que ya
# no existen tal cual en el schema v20 ("Currency_Hedged" eliminada, 100%
# redundante con Hedging_Policy; "Type" renombrada a "Vehicle_Structure";
# "Subtype" descompuesta en MMF_Structure/Alt_Strategy/Payoff_Profile).
# _load_all_from_bd() construye UN SOLO SELECT con TODAS las columnas de
# esta whitelist — al incluir una columna inexistente, sqlite3 lanza
# OperationalError en la query COMPLETA, capturado silenciosamente por el
# except, degradando TODOS los campos (no solo el/los inexistente/s) a None.
# Efecto: eff.get() ha estado devolviendo None para TODO campo, en TODO
# fondo, en cada ciclo del pipeline desde v20 — invalidando silenciosamente
# el propósito íntegro de EffectiveReader (recuperar valores de BD para
# fondos CACHED). Confirmado como causa raíz de la recurrencia de
# LU0637308312 (NORDEA) Investment_Universe='Liquidity': P04 no podía leer
# Geography='Global' de BD porque eff.get() devolvía None para TODOS los
# campos. "Subtype" se elimina sin reemplazo (descompuesta en 3 columnas,
# ninguna regla INTER actual las necesita); "Type" se renombra a
# "Vehicle_Structure".
_EFF_FIELDS_WHITELIST = frozenset({
    # Geográficos / Universo
    "Geography", "Investment_Universe", "Investment_Focus",
    # Cobertura de divisa
    "Hedging_Policy", "Fund_Currency",
    # Sectorial / temático
    "Sector_Focus", "Theme",
    # Benchmark
    "Benchmark_Declared", "Benchmark_Type",
    # Clasificación principal
    "Fund_Nature", "Vehicle_Structure", "Family",
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
        # Do NOT catch OperationalError here — assert_eff_fields_alignment()
        # must have run at startup and guaranteed all whitelist fields exist.
        # A silent swallow here was the root cause of the v20 silent null-out
        # (see FIX-EFF-CH header above).
        _ph = "%s" if is_postgres_connection(self._conn) else "?"
        row = self._conn.execute(
            f"SELECT {cols} FROM fund_master WHERE ISIN={_ph}",
            (self._isin,)
        ).fetchone()
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


def assert_eff_fields_alignment(conn: sqlite3.Connection) -> None:
    """
    Verifica que todos los campos de _EFF_FIELDS_WHITELIST existen como columnas
    en fund_master. Debe llamarse al inicio del pipeline (junto a
    assert_schema_alignment) para detectar drift entre la whitelist y el
    schema real antes de que EffectiveReader._load_all_from_bd() lo descubra
    en caliente con un OperationalError.

    Levanta AssertionError si algún campo de la whitelist no existe en el
    schema live — fail-fast en startup, antes de procesar cualquier fondo.
    """
    live_cols = table_columns(conn, "fund_master")
    if is_postgres_connection(conn):
        # table_columns() returns lowercase on PG (unquoted-identifier folding — see its own
        # docstring); fold the whitelist to match, same convention as schema_checks.py's
        # verify_db_schema(). PG folding means every whitelist field resolves to its real
        # lower_snake column with no rename needed (verified empirically, migration addendum §1).
        live_cols = {c.lower() for c in live_cols}
        missing = {f for f in _EFF_FIELDS_WHITELIST if f.lower() not in live_cols}
    else:
        missing = _EFF_FIELDS_WHITELIST - live_cols
    if missing:
        raise AssertionError(
            f"_EFF_FIELDS_WHITELIST contiene {len(missing)} campo(s) ausentes "
            f"en fund_master: {sorted(missing)}. "
            "Actualiza _EFF_FIELDS_WHITELIST en core/_db_utils.py para "
            "reflejar el schema actual (quita campos renombrados/eliminados, "
            "o aplica la migración de schema pendiente)."
        )
