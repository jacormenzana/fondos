# proyecto1/tests/test_altrv_bl44_fixes_20260731.py
# -*- coding: utf-8 -*-
"""
Regression tests for three audit fixes (pipelineP1Audit 2026-07-31):

FIX-ALTRV-TBILL-1
    detect_nature_from_kiid: add "treasury bill" to has_cash_bench patterns.
    Root cause: Franklin Alternative Strategies Fund (UNKNOWN-format KIID) has
    "absolute return" AR language in the objective + "3-month Treasury Bill
    Index" benchmark reference, but has_cash_bench was False (T-Bill is not
    €STR/EONIA/SOFR). Result: Mixtos instead of Alternativo.
    Fix: "treasury bill" added to has_cash_bench.

FIX-ALTRV-HEDGEFUND-1
    detect_nature_from_kiid: add "fondos de inversión libre" (Spanish regulatory
    term for hedge funds) to has_ar patterns; add _ar_in_pre_header check
    (fund product name, first 600 chars) guarded by _has_bond_in_header; extend
    Alternativo condition to accept _ar_in_pre_header as corroborator.
    Root cause: GS Absolute Return Tracker Portfolio DDF KIID has the fund name
    "absolute return" at position 425 (before the 500-char DDF window) and uses
    "fondos de inversión libre" in the objective to describe its mandate.
    Neither triggered has_ar or _ar_in_header → Mixtos instead of Alternativo.

FIX-BL44-RFC-SRRI4-1
    pipeline.py BL-44 RFCP threshold aligned with _NATURE_VOL_BANDS:
    {2,3,4} includes band 4, so RFCP+SRRI=4 is consistent, not anomalous.
    Old threshold >= 4 reclassified BSF EM DURAT BOND E EURHDG ACC (SRRI=4)
    to Restantes. Changed to >= 5. Tested here via the vol-bands constants,
    not via pipeline.py (R-7).

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_TESTS_DIR, "..", "core"))
for _p in (_CORE_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from classify_utils import detect_nature_from_kiid, _NATURE_VOL_BANDS

# ---------------------------------------------------------------------------
# Fixtures — minimal KIID excerpts that isolate the tested signals
# ---------------------------------------------------------------------------

# FRANKLIN ALTERNATIVE STRATEGIES style: AR objective + treasury bill benchmark
# Note: fixture must NOT contain "Documento de datos fundamentales" as leading text,
# to keep the format as UNKNOWN (window [200, 4500]).  DDF format (window [500, …])
# would push "en cualquier entorno de mercado" before the window start.
_FRANKLIN_ALT_KIID = (
    "Clase A (acc) EUR-H1 • ISIN LU1093756242 • Un subfondo de Franklin Templeton "
    "Investment Funds (OICVM). "
    "¿Qué es este producto?\n"
    "Tipo El producto es una clase de acciones del subfondo Franklin Alternative "
    "Strategies Fund A (acc) EUR-H1, que es un OICVM de duración indefinida. "
    "Duración recomendada: mínimo 5 años. "
    "Objetivos El Fondo tiene como objetivo generar rentabilidades positivas en "
    "cualquier entorno de mercado mediante el empleo de estrategias de inversión "
    "alternativas. "
    "El Fondo hace un seguimiento del rendimiento del índice ICE BofA US 3-month "
    "Treasury Bill Index y del HFRX Global Hedge Fund Index. "
    "El Fondo puede invertir en renta variable, renta fija y derivados. "
    "Indicador de riesgo: 3 / 7."
)

# GS ABSOLUTE RETURN TRACKER style: hedge-fund replication language
_GS_AR_TRACKER_KIID_HEADER = (
    "Finalidad Este documento le proporciona información fundamental que debe "
    "conocer sobre este producto de inversión. No se trata de material comercial. "
    "\nProducto Goldman Sachs Absolute Return Tracker Portfolio, un subfondo de "
    "Goldman Sachs Funds SICAV. ISIN: LU1103307408. "
    "Goldman Sachs Asset Management B.V. es el productor del PRIIP. "
    "Fecha de este DDF: 13/02/2026. "
)
_GS_AR_TRACKER_KIID_OBJECTIVE = (
    "¿Qué es este producto? "
    "Tipo Goldman Sachs Funds es un SICAV OICVM. "
    "Objetivos El objetivo de inversión de la Cartera es implementar una "
    "estrategia de negociación que trata de obtener un rendimiento cercano al "
    "de los fondos de inversión libre en su conjunto. "
    "La Cartera trata de aproximarse al componente beta adquiriendo exposición "
    "a varias clases de activos a los que están expuestos los fondos de "
    "inversión libre. "
    "Indicador de riesgo: 4 / 7."
)
_GS_AR_TRACKER_FULL_KIID = _GS_AR_TRACKER_KIID_HEADER + _GS_AR_TRACKER_KIID_OBJECTIVE

# BSF EM SHORT DURATION BOND: RFCP with SRRI=4 (should NOT be Restantes)
_BSF_EM_SHORT_DUR_KIID = (
    "Documento de datos fundamentales\n"
    "Producto BlackRock Emerging Markets Short Duration Bond Fund, Class E2 "
    "Hedged EUR. ISIN: LU1706560247. "
    "BlackRock (Luxembourg) S.A. Fecha: 15 abril 2026. "
    "¿Qué es este producto? "
    "Tipo El Fondo es un subfondo de BlackRock Strategic Funds, un OICVM. "
    "Objetivos El Fondo trata de maximizar el rendimiento total invirtiendo "
    "principalmente en bonos y otros títulos de deuda de mercados emergentes a "
    "corto plazo. "
    "El Fondo invierte en bonos denominados en dólares estadounidenses emitidos "
    "por entidades de mercados emergentes con una duración de cartera corta. "
    "Indicador de riesgo: 4 / 7."
)

# Guard: genuine bond fund with T-Bill comparison must NOT become Alternativo
_BOND_TBILL_NO_AR_KIID = (
    "Documento de datos fundamentales\n"
    "Producto European Corporate Bond Fund. ISIN: LU9999999999. "
    "Objetivos El Fondo invierte principalmente en bonos corporativos europeos "
    "con grado de inversión. "
    "El objetivo es superar el rendimiento del 3-month Treasury Bill Index "
    "en un 200 pbs anuales sobre un ciclo de mercado completo. "
    "Indicador de riesgo: 3 / 7."
)

# Guard: "absolute return bond" fund must NOT trigger _ar_in_pre_header
_AR_BOND_FUND_KIID = (
    "Documento de datos fundamentales\n"
    "Producto PIMCO Absolute Return Bond Fund. ISIN: LU9999999998. "
    "Objetivos El Fondo invierte en una amplia gama de instrumentos de renta "
    "fija globales buscando generar retorno absoluto con baja correlación con "
    "los mercados de renta fija tradicionales. "
    "El objetivo de rendimiento del Fondo supera el 3-month Treasury Bill. "
    "Indicador de riesgo: 3 / 7."
)


# ---------------------------------------------------------------------------
# FIX-ALTRV-TBILL-1 — treasury bill as cash-bench proxy
# ---------------------------------------------------------------------------
class TestFranklinAltStrategiesAlternativo:
    """AR + Treasury Bill benchmark → Alternativo."""

    def test_franklin_alt_str_is_alternativo(self):
        result = detect_nature_from_kiid(_FRANKLIN_ALT_KIID)
        assert result == "Alternativo", (
            f"Franklin Alternative Strategies must be Alternativo; got {result!r}"
        )

    def test_treasury_bill_alone_no_ar_stays_non_alternativo(self):
        """T-Bill mention without AR language must NOT trigger Alternativo."""
        result = detect_nature_from_kiid(_BOND_TBILL_NO_AR_KIID)
        assert result != "Alternativo", (
            "Bond fund with T-Bill comparison but no AR language must not become "
            f"Alternativo; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-ALTRV-HEDGEFUND-1 — fondos de inversión libre + pre-header AR
# ---------------------------------------------------------------------------
class TestGsAbsoluteReturnTrackerAlternativo:
    """Hedge-fund tracker: "fondos de inversión libre" + pre-header AR → Alternativo."""

    def test_gs_ar_tracker_is_alternativo(self):
        result = detect_nature_from_kiid(_GS_AR_TRACKER_FULL_KIID)
        assert result == "Alternativo", (
            f"GS Absolute Return Tracker must be Alternativo; got {result!r}"
        )

    def test_hedgefund_language_plus_cashbench_is_alternativo(self):
        """'fondos de inversión libre' (has_ar) + overnight rate (has_cash_bench)
        must resolve to Alternativo without needing the pre-header path."""
        # UNKNOWN format text: window starts at 200.  Pad header to put objective after 200.
        kiid = (
            "Clase B EUR ISIN LU0000000001 Un subfondo de Test SICAV. "
            "Tipo Fondo OICVM de gestión alternativa con horizonte a 5 años. "
            "Objetivos El objetivo de inversión de la Cartera es implementar una "
            "estrategia que trata de obtener un rendimiento cercano al de los "
            "fondos de inversión libre en su conjunto. "
            "El rendimiento se mide contra la tasa overnight de referencia del BCE. "
            "Indicador de riesgo: 4 / 7."
        )
        result = detect_nature_from_kiid(kiid)
        assert result == "Alternativo", (
            f"'fondos de inversión libre' + overnight bench must resolve to Alternativo; "
            f"got {result!r}"
        )

    def test_absolute_return_bond_pre_header_guard(self):
        """'absolute return bond' in the product name has 'bond' → _has_bond_in_header
        True → _ar_in_pre_header False → must NOT trigger Alternativo via pre-header."""
        result = detect_nature_from_kiid(_AR_BOND_FUND_KIID)
        assert result != "Alternativo", (
            "An 'Absolute Return Bond' fund guarded by bond-in-header must not "
            f"become Alternativo via pre-header; got {result!r}"
        )


# ---------------------------------------------------------------------------
# FIX-BL44-RFC-SRRI4-1 — vol-bands consistency: SRRI=4 is valid for RFCP
# ---------------------------------------------------------------------------
class TestRfcpVolBandIncludesBand4:
    """_NATURE_VOL_BANDS must include band 4 for RFCP — this is the calibrated
    upper boundary for EM credit / covered bonds.  BL-44 threshold must be >= 5,
    not >= 4.  Validated via constants, not pipeline.py (R-7)."""

    def test_band_4_in_rfcp_vol_bands(self):
        rfcp_bands = _NATURE_VOL_BANDS.get("Renta Fija Corto Plazo", set())
        assert 4 in rfcp_bands, (
            f"Band 4 must be in RFCP vol-bands; got {rfcp_bands}"
        )

    def test_band_5_not_in_rfcp_vol_bands(self):
        """Band 5 is Mixtos/RV territory and must NOT be in RFCP bands."""
        rfcp_bands = _NATURE_VOL_BANDS.get("Renta Fija Corto Plazo", set())
        assert 5 not in rfcp_bands, (
            "Band 5 is outside RFCP territory and must NOT be in its vol-bands"
        )

    def test_rfcp_vol_band_set_is_correct(self):
        """_NATURE_VOL_BANDS["Renta Fija Corto Plazo"] must be exactly {2,3,4} —
        the calibrated range that allows EM credit / covered bonds at SRRI=4."""
        rfcp_bands = _NATURE_VOL_BANDS.get("Renta Fija Corto Plazo", set())
        assert rfcp_bands == {2, 3, 4}, (
            f"RFCP vol-bands must be {{2,3,4}}; got {rfcp_bands}"
        )
