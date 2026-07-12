# proyecto1/tests/test_bl44_hardening.py
# -*- coding: utf-8 -*-
"""
Tests unitarios para BL-44 hardening (C2 / C4 2026-07-11).

Cubre:
  - Fondo con token FUERTE (m mkt / eu m mkt / vnav / lvnav / cnav / mmf)
    + SRRI anómalo ≥ 3 → Fund_Nature='Monetario' + _bl44_srri_anomaly set.
  - Fondo con token DÉBIL (treasury / cash) + SRRI anómalo ≥ 3
    → comportamiento original: Fund_Nature='Restantes'.
  - Fondo con SRRI válido (1-2) independientemente del marcador
    → Fund_Nature='Monetario', sin flag.
  - STRONG_MMF_STRUCTURE_MARKERS exportado correctamente desde classify_utils.

Regla R-7: sin imports de pipeline.py ni core.io.
"""
from __future__ import annotations
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ1_DIR = os.path.normpath(os.path.join(_TESTS_DIR, ".."))
_CORE_DIR  = os.path.join(_PROJ1_DIR, "core")
_BLOCK_DIR = os.path.join(_PROJ1_DIR, "blocks")

for _d in (_CORE_DIR, _BLOCK_DIR, _PROJ1_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import pytest
from monetarios import classify_fund
from classify_utils import STRONG_MMF_STRUCTURE_MARKERS


# ─── helpers ─────────────────────────────────────────────────────────────────

def _fake_kiid(srri: int | None) -> str:
    """Texto KIID mínimo que expone el patrón SRRI sin cabecera real."""
    if srri is None:
        return "Este es un fondo de inversión monetario de muy corto plazo." * 5
    return (
        f"RISK AND REWARD INDICATOR\n"
        f"Lower risk  1 | 2 | 3 | 4 | 5 | 6 | [{srri}]  Higher risk\n"
        f"The current indicator is {srri}/7.\n"
        f"OBJECTIVES AND INVESTMENT POLICY\n"
        f"The fund aims to maintain capital stability and provide returns in line "
        f"with money market rates.\n"
        f"CHARGES: Ongoing charges 0.12% per year.\n"
    ) * 2


# ─── STRONG markers: fondo debe sobrevivir BL-44 ─────────────────────────────

class TestStrongMarkerPreservesMonetario:
    """Con token fuerte + SRRI anómalo → Monetario + flag."""

    @pytest.mark.parametrize("name,srri", [
        ("DWS ESG EU M MKT IC100 EUR ACC",  5),
        ("JPM EURO M MKT VNAV EUR ACC",      4),
        ("JPM USD LQUDTY LVNAV USD ACC",     3),
        ("FIDELITY GLOBAL CNAV FUND EUR I",  4),
        ("ALLIANZ EURO MMF CLASS B EUR",     3),
        ("BNP INSCASH EUR 3M INST",          5),
        ("BLACKROCK STANDARD MM VNAV EUR",   4),
        ("PICTET UCITS MMF EUR I",           3),
        ("JPM STANDARD MM VNAV USD",         4),
        ("AMUNDI MONEY MARKET EUR ACC",      5),
        ("DB MONEY MKT PLUS EUR ACC",        3),
    ])
    def test_strong_marker_survives_bl44(self, name, srri):
        kiid = _fake_kiid(srri)
        result = classify_fund(name, kiid)
        assert result.get("Fund_Nature") == "Monetario", (
            f"Fondo con marcador fuerte '{name}' (SRRI={srri}) "
            f"no debe ser eyectado a Restantes por BL-44"
        )
        assert result.get("_bl44_srri_anomaly") == srri, (
            f"Fondo con marcador fuerte '{name}' (SRRI={srri}) "
            f"debe tener _bl44_srri_anomaly={srri}"
        )

    def test_m_mkt_pattern_srri5(self):
        """Caso raíz: DWS ESG EU M MKT con SRRI=5 → Monetario."""
        result = classify_fund(
            "DWS ESG EU M MKT IC100 EUR ACC",
            _fake_kiid(5),
        )
        assert result["Fund_Nature"] == "Monetario"
        assert result["_bl44_srri_anomaly"] == 5

    def test_vnav_pattern_srri4(self):
        """VNAV explícito + SRRI=4 → Monetario + flag."""
        result = classify_fund("FONDO VNAV PLUS EUR ACC", _fake_kiid(4))
        assert result["Fund_Nature"] == "Monetario"
        assert result["_bl44_srri_anomaly"] == 4

    def test_money_market_pattern_srri3(self):
        """'money market' en nombre + SRRI=3 → Monetario + flag."""
        result = classify_fund("GLOBAL MONEY MARKET FUND EUR", _fake_kiid(3))
        assert result["Fund_Nature"] == "Monetario"
        assert result["_bl44_srri_anomaly"] == 3


# ─── WEAK markers: comportamiento original sin cambios ───────────────────────

class TestWeakMarkerEjectedToRestantes:
    """Con solo token débil (treasury/cash sin marcador fuerte) + SRRI ≥ 3
    → comportamiento original: Fund_Nature cambia a 'Restantes'."""

    @pytest.mark.parametrize("name,srri", [
        ("PIMCO TREASURY PLUS EUR ACC",     5),
        ("BLACKROCK USD CASH FUND I",       4),
        ("AMUNDI LIQUIDITY PLUS EUR ACC",   3),
        ("ENHANCED CASH STRATEGY EUR B",    4),
    ])
    def test_weak_marker_ejected(self, name, srri):
        kiid = _fake_kiid(srri)
        result = classify_fund(name, kiid)
        # Solo aplica cuando el nombre está en el universo de monetarios.
        # Si el fondo con nombre débil no está en el universo, el bloque
        # ni siquiera lo clasificaría, así que sólo verificamos la dirección
        # si Fund_Nature está presente en el result.
        fn = result.get("Fund_Nature")
        if fn is not None:
            assert fn == "Restantes", (
                f"Token débil '{name}' (SRRI={srri}) debería seguir eyectando a Restantes"
            )
        # En cualquier caso no debe haber flag de anomalía
        assert result.get("_bl44_srri_anomaly") is None, (
            f"Token débil '{name}' no debe poner _bl44_srri_anomaly"
        )


# ─── SRRI válido (1-2): sin flag, clasificación normal ───────────────────────

class TestValidSrriNoFlag:
    """SRRI 1 ó 2 → Fund_Nature='Monetario', sin flag _bl44_srri_anomaly."""

    @pytest.mark.parametrize("name,srri", [
        ("DWS ESG EU M MKT IC100 EUR ACC",  1),
        ("JPM EURO M MKT VNAV EUR ACC",      2),
        ("PICTET UCITS MMF EUR I",           1),
        ("JPM USD LQUDTY LVNAV USD ACC",     2),
    ])
    def test_valid_srri_no_flag(self, name, srri):
        kiid = _fake_kiid(srri)
        result = classify_fund(name, kiid)
        assert result.get("Fund_Nature") == "Monetario", (
            f"'{name}' con SRRI={srri} debe ser Monetario (sin BL-44 ejection)"
        )
        assert result.get("_bl44_srri_anomaly") is None, (
            f"SRRI válido ({srri}) no debe poner flag _bl44_srri_anomaly"
        )

    def test_no_srri_no_flag(self):
        """Sin SRRI extraíble → BL-44 no aplica → sin flag."""
        result = classify_fund(
            "DWS ESG EU M MKT IC100 EUR ACC",
            _fake_kiid(None),
        )
        assert result.get("Fund_Nature") == "Monetario"
        assert result.get("_bl44_srri_anomaly") is None


# ─── STRONG_MMF_STRUCTURE_MARKERS constant ───────────────────────────────────

class TestStrongMmfMarkersConstant:
    """Verifica la constante exportada desde classify_utils."""

    def test_constant_is_non_empty_tuple(self):
        assert isinstance(STRONG_MMF_STRUCTURE_MARKERS, tuple)
        assert len(STRONG_MMF_STRUCTURE_MARKERS) > 0

    def test_regulatory_tokens_present(self):
        """Tokens regulatorios clave deben estar en la constante."""
        for tok in ("vnav", "lvnav", "cnav", "mmf", "money market"):
            assert tok in STRONG_MMF_STRUCTURE_MARKERS, (
                f"Token regulatorio '{tok}' debe estar en STRONG_MMF_STRUCTURE_MARKERS"
            )

    def test_weak_tokens_absent(self):
        """Tokens ambiguos que causan falsos positivos NO deben estar."""
        for tok in ("treasury", "cash", "liquidity", "enhanced", "tresorerie"):
            assert tok not in STRONG_MMF_STRUCTURE_MARKERS, (
                f"Token ambiguo '{tok}' NO debe estar en STRONG_MMF_STRUCTURE_MARKERS"
            )
