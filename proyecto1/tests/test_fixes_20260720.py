# test_fixes_20260720.py
# R-7: no pipeline.py / core.io imports.
# Covers:
#   FIX-RFC-EN-NEGATION-1   — English "fixed maturity" negation guard
#   FIX-SECTOR-FINSERV-1    — Financial Services theme in detect_theme_from_kiid
#   FIX-GEO-EDR-1           — umbrella-SICAV "vehículo que tiene por objeto" guard

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from proyecto1.core.classify_utils import detect_geography_from_kiid
from proyecto1.core.fund_characterizer import detect_theme_from_kiid, detect_sector_focus

# ── window helpers ────────────────────────────────────────────────────────────
# UNKNOWN format → window [200:4500]; pad so the objective lands inside.
_PAD200 = "Product information.\n" * 10  # ~200 chars

def _en_kiid(objective: str) -> str:
    return _PAD200 + "Objective\n" + objective

# DDF format → window [500:5000]
_DDF_HDR = (
    "Documento de Datos Fundamentales\n\n"
    "Finalidad Este documento le proporciona información fundamental.\n\n"
    "Producto Fondo Ejemplo, ISIN: XX0000000000\n"
    "Este Documento de datos fundamentales tiene fecha de 01/01/2026.\n\n"
)
_DDF_PAD = " " * max(0, 500 - len(_DDF_HDR))

def _ddf(objective: str) -> str:
    return _DDF_HDR + _DDF_PAD + "Objetivos " + objective


# ── FIX-RFC-EN-NEGATION-1 ────────────────────────────────────────────────────

from proyecto1.core.classify_utils import resolve_rf_subtype

_EM_DEBT_KIID = _en_kiid(
    "At least 70% of the Fund's investments will be in fixed income securities (e.g. bonds). "
    "The Fund does not have a fixed maturity. All the shares in the Fund may be redeemed "
    "by the board of directors. The fund invests in fixed income securities issued by "
    "corporations and governmental agencies in emerging markets countries. "
    "Investment Objective: To seek to maximise total return."
)


def test_en_fixed_maturity_negated_gives_rff():
    """'does not have a fixed maturity' must NOT trigger RF_Corto."""
    result = resolve_rf_subtype("ms inv fd emerg debt i usd acc", _EM_DEBT_KIID)
    assert result == "RF_Flexible", (
        f"EN 'does not have a fixed maturity' should give RF_Flexible, got {result!r}"
    )


def test_en_no_fixed_maturity_negated():
    """'no fixed maturity' variant also suppresses the signal."""
    kiid = _en_kiid(
        "The fund has no fixed maturity. It invests in investment grade bonds globally. "
        "Objective: to provide income and capital preservation."
    )
    result = resolve_rf_subtype("global bond fund acc", kiid)
    assert result == "RF_Flexible", (
        f"EN 'has no fixed maturity' should give RF_Flexible, got {result!r}"
    )


def test_es_no_tiene_fecha_vencimiento_still_rff():
    """Regression: Spanish 'no tiene fecha de vencimiento' still gives RF_Flexible."""
    kiid = _ddf(
        "El Fondo no tiene fecha de vencimiento. Invierte en bonos corporativos globales "
        "de alta calificación crediticia. El objetivo es proporcionar ingresos estables."
    )
    result = resolve_rf_subtype("global corporate bond acc", kiid)
    assert result == "RF_Flexible", (
        f"ES 'no tiene fecha de vencimiento' should give RF_Flexible, got {result!r}"
    )


def test_target_maturity_still_rfc():
    """Regression: genuine target-maturity fund still gives RF_Corto."""
    kiid = _ddf(
        "El Fondo tiene como objetivo proporcionar rendimientos hasta la fecha "
        "de vencimiento fijo de diciembre 2028 (target maturity). Invierte en "
        "bonos con grado de inversión con vencimiento antes de diciembre 2028."
    )
    result = resolve_rf_subtype("credit 2028 a eur acc", kiid)
    assert result == "RF_Corto", (
        f"target maturity fund should give RF_Corto, got {result!r}"
    )


def test_genuine_fixed_maturity_rfc():
    """'vencimiento fijo' (genuine) still gives RF_Corto."""
    kiid = _ddf(
        "El Fondo tiene una fecha de vencimiento fijo en diciembre 2027. "
        "Invierte principalmente en bonos de corto plazo con vencimiento anterior."
    )
    result = resolve_rf_subtype("horizon 2027 eur acc", kiid)
    assert result == "RF_Corto", (
        f"genuine 'vencimiento fijo' should give RF_Corto, got {result!r}"
    )


# ── FIX-SECTOR-FINSERV-1 ─────────────────────────────────────────────────────

def test_sector_servicios_financieros_detects_finserv():
    """Primary 'sector de servicios financieros' → Financial Services theme."""
    kiid = _ddf(
        "El objetivo del Fondo es lograr revalorización del capital invirtiendo "
        "principalmente en acciones de empresas del sector de servicios financieros "
        "globales, incluyendo bancos, compañías de seguros y gestoras de activos. "
        "El Fondo también podrá, en menor medida, invertir en el sector inmobiliario."
    )
    theme = detect_theme_from_kiid(kiid)
    assert theme == "Financial Services", (
        f"'sector de servicios financieros' should give Financial Services, got {theme!r}"
    )


def test_financial_services_sector_en():
    """'financial services sector' (EN) → Financial Services theme."""
    kiid = _en_kiid(
        "The Fund invests primarily in equities of companies in the financial services "
        "sector globally, including banks, insurance companies, and diversified financials."
    )
    theme = detect_theme_from_kiid(kiid)
    assert theme == "Financial Services", (
        f"EN 'financial services sector' should give Financial Services, got {theme!r}"
    )


def test_detect_sector_focus_finserv():
    """detect_sector_focus with Financial Services theme → 'Financial Services'."""
    kiid = _ddf(
        "El Fondo invierte en acciones de empresas del sector de servicios financieros "
        "globales o relacionadas con él."
    )
    theme = detect_theme_from_kiid(kiid)
    sf = detect_sector_focus("algebris financia r usdhdg acc", kiid, theme)
    assert sf == "Financial Services", (
        f"detect_sector_focus should return 'Financial Services', got {sf!r}"
    )


def test_real_estate_primary_still_works():
    """Regression: primary real estate fund still gives Real Estate theme."""
    kiid = _ddf(
        "El Fondo invierte principalmente en empresas inmobiliarias cotizadas (REITs). "
        "La estrategia se centra en el sector inmobiliario global diversificado."
    )
    theme = detect_theme_from_kiid(kiid)
    assert theme == "Real Estate", (
        f"Primary real-estate fund should give Real Estate, got {theme!r}"
    )


def test_finserv_before_real_estate_wins():
    """Financial Services signal earlier in text wins over secondary Real Estate."""
    kiid = _ddf(
        "El Fondo invierte principalmente en acciones del sector bancario y servicios "
        "financieros globales. En menor medida, el Fondo puede acceder al "
        "sector inmobiliario como asignación secundaria."
    )
    theme = detect_theme_from_kiid(kiid)
    assert theme == "Financial Services", (
        f"Primary FinServ + secondary RealEstate should give Financial Services, got {theme!r}"
    )


# ── FIX-GEO-EDR-1 ─────────────────────────────────────────────────────────────

def test_vehiculo_que_tiene_por_objeto_suppresses_emergentes():
    """Umbrella SICAV description suppresses 'países emergentes' geo signal."""
    kiid = _ddf(
        "El Fondo invierte en valores de renta variable y renta fija europeos que "
        "ofrezcan rendimientos atractivos. "
        "Este producto es un vehículo que tiene por objeto, en particular, la "
        "inversión en empresas registradas predominantemente en países emergentes. "
        "Esta acción está destinada a inversores minoristas."
    )
    result = detect_geography_from_kiid(kiid)
    assert result != "Emergentes", (
        f"Umbrella SICAV 'vehículo que tiene por objeto' should suppress Emergentes, "
        f"got {result!r}"
    )


def test_genuine_em_mandate_still_detects_emergentes():
    """Genuine primary EM mandate (no umbrella context) still gives Emergentes."""
    kiid = _ddf(
        "El Fondo invierte principalmente en valores de renta fija emitidos por "
        "gobiernos y empresas de países emergentes. La mayor parte de los activos "
        "estará en mercados de países emergentes de Asia, América Latina y Europa del Este."
    )
    result = detect_geography_from_kiid(kiid)
    assert result == "Emergentes", (
        f"Genuine EM primary mandate should give Emergentes, got {result!r}"
    )
