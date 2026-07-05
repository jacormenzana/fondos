# proyecto1/tests/test_restantes_universe.py
# -*- coding: utf-8 -*-
"""
Tests unitarios de restantes.get_universe_isins() — FIX-RESTANTES-UNIVERSE
(2026-07-05).

Antes de este fix, restantes solo recogía:
  A) ISINs del Excel no presentes en fund_master (primera ejecución).
  B) ISINs con Heuristic_Block='RESTANTES' en fund_master.

Esto dejaba ~882 fondos "huérfanos": presentes en fund_master con el
Heuristic_Block de un bloque primario (p.ej. 'RENTA_VARIABLE') cuya
heurística de nombre VIGENTE ya no los reclamaba. Estos fondos nunca
eran revisados y quedaban con clasificación congelada.

El fix añade el Escenario C: ISINs ya en fund_master cuyo bloque de
origen ya no los reclama hoy, pero que tampoco están en ningún otro
bloque activo — es decir, el complemento del conjunto de ISINs
reclamados por todos los bloques primarios activos hoy.

Patrones de prueba
-------------------
Estos tests usan DataFrames mínimos construidos en memoria para que
no dependan del Excel real ni de fund_master de producción — cumple R-7.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Sys.path bootstrap (igual que test_data_quality_rollup.py)
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
_P1_DIR    = os.path.normpath(os.path.join(_TESTS_DIR, '..'))
_ROOT_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', '..'))
for _p in (_CORE_DIR, _P1_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sqlite_writer import create_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_conn():
    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _seed_fund_master(conn, isin: str, block: str, name: str = "TEST FUND") -> None:
    """Inserta una fila mínima en fund_master (solo columnas NOT NULL)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO fund_master
            (ISIN, Fund_Name, Fund_Nature, Heuristic_Block, Heuristic_Core)
        VALUES (?, ?, 'Restantes', ?, 0)
        """,
        (isin, name, block),
    )
    conn.commit()


def _seed_wrong_doc(conn, isin: str) -> None:
    """Marca un ISIN como WRONG_DOC en fund_kiid_metadata."""
    conn.execute(
        """
        INSERT OR IGNORE INTO fund_kiid_metadata
            (ISIN, KIID_Class, KIID_Status)
        VALUES (?, 1, 'WRONG_DOC')
        """,
        (isin,),
    )
    conn.commit()


def _make_df(*rows) -> pd.DataFrame:
    """
    Construye un df_master mínimo.
    Cada row: (isin, fund_name) o (isin,) — usa fund_name='TEST FUND' por defecto.
    """
    data = []
    for row in rows:
        isin = row[0]
        name = row[1] if len(row) > 1 else "TEST FUND"
        data.append({"ISIN": isin, "Fund_Name": name, "Management_Company": "Test"})
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import blocks.restantes as restantes
from pipeline import _is_valid_isin


# ---------------------------------------------------------------------------
# Tests: _is_valid_isin (helper en pipeline.py, FIX-MASTER-LOAD-2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("isin,expected", [
    ("LU0173778175", True),      # ISIN válido real
    ("IE00B4L5Y983", True),      # ISIN válido real
    ("ES0137921020", True),      # ISIN válido real (país ES)
    ("XS2038988882", True),      # ISIN válido real (mercado global)
    ("C\xf3digo ISIN", False),   # header row de Franklin
    ("Código ISIN",   False),    # ídem, versión sin escape
    ("",              False),    # vacío
    ("LU012345",      False),    # demasiado corto
    ("LU01234567891234", False), # demasiado largo
    (None,            False),    # tipo incorrecto
    (12345678901,     False),    # tipo entero
])
def test_is_valid_isin(isin, expected):
    assert _is_valid_isin(isin) is expected


# ---------------------------------------------------------------------------
# Tests: restantes.get_universe_isins()
# ---------------------------------------------------------------------------

class TestEscenarioA:
    """ISIN en master pero no en fund_master → incluido."""

    def test_absent_from_db_is_included(self):
        conn = _memory_conn()
        df   = _make_df(("LU0000000001",))
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000001" in result

    def test_present_in_db_tagged_other_block_claimed_today_is_excluded(self):
        """
        Fondo en master Y en fund_master con Heuristic_Block='RENTA_VARIABLE'
        cuyo Fund_Name aún coincide con el patrón include de renta_variable
        ('equity') → sigue siendo reclamado hoy → NO entra en Restantes.
        """
        conn = _memory_conn()
        # "GLOBAL EQUITY FUND" sigue matchando renta_variable (include: 'equity')
        df = _make_df(("LU0000000010", "GLOBAL EQUITY FUND"))
        _seed_fund_master(conn, "LU0000000010", "RENTA_VARIABLE",
                          name="GLOBAL EQUITY FUND")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000010" not in result


class TestEscenarioB:
    """ISIN con Heuristic_Block='RESTANTES' → incluido."""

    def test_tagged_restantes_is_included(self):
        conn = _memory_conn()
        df   = _make_df(("LU0000000002", "OPAQUE FUND XYZ"))
        _seed_fund_master(conn, "LU0000000002", "RESTANTES", name="OPAQUE FUND XYZ")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000002" in result


class TestEscenarioC:
    """
    Fondo huérfano: en fund_master con Heuristic_Block de otro bloque,
    pero ese bloque ya no lo reclama hoy → incluido.
    """

    def test_orphaned_fund_is_included(self):
        """
        'ZZZZ OPAQUE FUND' no contiene ningún patrón include de
        renta_variable (equity/shares/global/etc.) ni de ningún otro bloque.
        Antes de FIX-RESTANTES-UNIVERSE este fondo quedaba congelado
        indefinidamente; ahora Escenario C lo recupera.
        """
        conn = _memory_conn()
        df   = _make_df(("LU0000000003", "ZZZZ OPAQUE FUND"))
        # Lo insertamos como si hubiera sido clasificado por RENTA_VARIABLE
        # en un ciclo antiguo (cuyas heurísticas eran más amplias).
        _seed_fund_master(conn, "LU0000000003", "RENTA_VARIABLE",
                          name="ZZZZ OPAQUE FUND")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000003" in result

    def test_orphaned_fund_from_mixtos_is_included(self):
        """
        Un fondo que en el pasado tenía el tag MIXTOS pero cuyo nombre opaco
        ya no coincide con las heurísticas actuales de mixtos tampoco debe
        quedar huérfano.
        """
        conn = _memory_conn()
        df   = _make_df(("LU0000000007", "QQQQQ OPAQUE BALANCED"))
        # "OPAQUE BALANCED" no matchea mixtos (que requiere "balanced" solo,
        # pero de hecho "balanced" sí es un include en mixtos — usar nombre
        # que claramente no cuadra con ningún bloque).
        df   = _make_df(("LU0000000007", "ZZZQ OPAQUE FUND QQQQZ"))
        _seed_fund_master(conn, "LU0000000007", "MIXTOS",
                          name="ZZZQ OPAQUE FUND QQQQZ")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000007" in result


class TestWrongDocExclusion:
    """Fondos con KIID_Status='WRONG_DOC' excluidos de todos los escenarios."""

    def test_wrong_doc_excluded_from_escenario_a(self):
        conn = _memory_conn()
        df   = _make_df(("LU0000000004",))
        # No está en fund_master → Escenario A, pero tiene WRONG_DOC
        _seed_wrong_doc(conn, "LU0000000004")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000004" not in result

    def test_wrong_doc_excluded_from_escenario_b(self):
        conn = _memory_conn()
        df   = _make_df(("LU0000000005",))
        _seed_fund_master(conn, "LU0000000005", "RESTANTES")
        _seed_wrong_doc(conn, "LU0000000005")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000005" not in result

    def test_wrong_doc_excluded_from_escenario_c(self):
        conn = _memory_conn()
        df   = _make_df(("LU0000000006", "ZZZZ OPAQUE FUND WRONG"))
        _seed_fund_master(conn, "LU0000000006", "RENTA_VARIABLE",
                          name="ZZZZ OPAQUE FUND WRONG")
        _seed_wrong_doc(conn, "LU0000000006")
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000006" not in result


class TestConnNone:
    """Cuando conn=None → devuelve todos los ISINs del master (sorted)."""

    def test_conn_none_returns_all_master_isins(self):
        df = _make_df(
            ("LU0000000011",),
            ("LU0000000012",),
            ("LU0000000013",),
        )
        result = restantes.get_universe_isins(df, conn=None)
        assert result == sorted(["LU0000000011", "LU0000000012", "LU0000000013"])

    def test_conn_none_deduplicates(self):
        df = pd.DataFrame([
            {"ISIN": "LU0000000011", "Fund_Name": "X", "Management_Company": "A"},
            {"ISIN": "LU0000000011", "Fund_Name": "X", "Management_Company": "B"},
        ])
        result = restantes.get_universe_isins(df, conn=None)
        assert result.count("LU0000000011") == 1


class TestReturnOrder:
    """El resultado siempre está ordenado (sorted)."""

    def test_result_is_sorted(self):
        conn = _memory_conn()
        df = _make_df(
            ("LU0000000020",),
            ("LU0000000015",),
            ("LU0000000018",),
        )
        result = restantes.get_universe_isins(df, conn)
        assert result == sorted(result)


class TestNoFalseNegatives:
    """
    Un fondo que NO está en el master no entra aunque esté en fund_master.
    Escenario: datos legados en fund_master de un ISIN que fue retirado del
    Excel. Restantes no debe procesar ISINs ausentes del maestro.
    """

    def test_isin_not_in_master_is_excluded(self):
        conn = _memory_conn()
        # "LU0000000030" está en fund_master RESTANTES pero NO en df_master
        _seed_fund_master(conn, "LU0000000030", "RESTANTES")
        df = _make_df(("LU0000000031",))  # otro ISIN
        result = restantes.get_universe_isins(df, conn)
        assert "LU0000000030" not in result
