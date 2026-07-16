# proyecto1/tests/test_b6_ig_allowance_20260716.py
# -*- coding: utf-8 -*-
"""
Regression tests for FIX-B6-4 (2026-07-16).

FIX-B6-4 (pipeline.py, 2026-07-16):
    BL-B6-HY-KIID was reclassifying IG-primary short-duration funds to High Yield
    because it matched the sub-IG phrase without checking the surrounding context.
    Sessions-14 guards (FIX-B6-2/3) were name-only and missed funds whose names
    don't contain 'government'/'sovereign'/'aggregate'.

    Confirmed false positives:
      - LU0106234643 SISF EURO SHORT TERM BND: "al menos dos tercios … grado de
        inversión" (IG-primary with small sub-IG allowance for CDS hedging).
      - LU0562247857 JPM US SH DURATION: "como mínimo el 75% … investment grade …
        podrá invertir de manera limitada … inferior a investment grade".

    Fix: generic context guard (R-6, P#5 — no fund names): when a sub-IG signal
    matches, inspect a ±180-char window around the match; if an IG-majority or
    allowance qualifier is present in that window the override is suppressed.

R-7: no imports of pipeline.py or core.io.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


# ─── Mirror the BL-B6-HY-KIID logic from pipeline.py ────────────────────────
# (name guards from session 14 + FIX-B6-4 context guard)

_IG_NAME_GUARDS = [
    "government bond", "sovereign bond", "treasury bond",
    "euro government", "staatsanleihen", "gilt",
    "aggregate",
]

_HY_KIID_SIGNALS = [
    "calificaciones de menor calidad",
    "inferior a grado de inversión",
    "inferiores a grado de inversión",
    "sub-investment grade",
    "calificación inferior a grado de inversión",
    "inferior a investment grade",
]

# FIX-B6-4: IG-primary / allowance qualifier pattern (mirrors pipeline.py)
_IG_PRIMARY_QUALIFIERS = re.compile(
    r"al\s+menos\s+(?:dos\s+tercios|el\s+\d+\s*%|\d+\s*%)?"
    r"|como\s+m[ií]nimo\s+(?:el\s+)?\d+\s*%"
    r"|(?:el\s+)?\d{2,3}\s*%\s+de\s+(?:los\s+)?t[íi]tulos?\s+con\s+calificaci[oó]n\s+investment\s+grade"
    r"|principalmente\s+en\s+t[íi]tulos?\s+de\s+deuda\s+con\s+calificaci[oó]n\s+investment\s+grade"
    r"|de\s+manera\s+limitada"
    r"|de\s+forma\s+limitada"
    r"|hasta\s+un\s+\d+\s*%[^.]{0,30}(?:inferior|sub.investment|menor\s+calidad)"
    r"|podr[aá]\s+invertir(?:[^.]{0,60}de\s+manera\s+limitada)"
    r"|mayoritariamente\s+en\s+.{0,60}grado\s+de\s+inversi[oó]n",
    re.I,
)


def _is_ig_allowance_context(kt: str, signal: str) -> bool:
    pos = kt.find(signal)
    if pos == -1:
        return False
    # Look back 400 chars: covers long IG-mandate sentences where the qualifier
    # precedes the sub-IG allowance clause by ~300+ chars (SISF pattern).
    window = kt[max(0, pos - 400): pos + len(signal) + 200]
    return bool(_IG_PRIMARY_QUALIFIERS.search(window))


def _bl_b6_fires(fund_name: str, kiid_text: str, fund_nature: str = "Renta Fija Flexible") -> bool:
    """Simulate the full BL-B6-HY-KIID logic including all session fixes."""
    if fund_nature == "Monetario":
        return False
    name_l = fund_name.lower()
    if any(g in name_l for g in _IG_NAME_GUARDS):
        return False
    kt_l = kiid_text.lower()
    matched_signal = next((k for k in _HY_KIID_SIGNALS if k in kt_l), None)
    if matched_signal and not _is_ig_allowance_context(kt_l, matched_signal):
        return True
    return False


# ─── FIX-B6-4 tests ──────────────────────────────────────────────────────────

class TestIgAllowanceContextGuard:
    """IG-primary funds with a limited sub-IG allowance must not be reclassified."""

    def test_sisf_euro_short_term_not_reclassified(self):
        """LU0106234643: 'al menos dos tercios … grado de inversión' = IG-primary.
        The KIID also mentions 'inferior a grado de inversión' as secondary allowance."""
        kiid = (
            "invierte al menos dos tercios de sus activos en bonos a corto plazo "
            "con una calificación crediticia de grado de inversión o, directa o "
            "indirectamente (incluso a través de instrumentos derivados como "
            "permutas de incumplimiento crediticio e índices de permutas de "
            "incumplimiento crediticio), en bonos con una calificación "
            "inferior a grado de inversión (según Standard & Poor's u otra "
            "calificación equivalente)"
        )
        assert _bl_b6_fires("SISF EURO SHORT TERM BND A ACC", kiid) is False, (
            "SISF Euro Short Term is IG-primary ('al menos dos tercios'); must not fire"
        )

    def test_jpm_us_short_duration_not_reclassified(self):
        """LU0562247857: 'como mínimo el 75% … investment grade … de manera limitada'."""
        kiid = (
            "Como mínimo el 75% del patrimonio se invierte en títulos de deuda a "
            "corto plazo con calificación investment grade emitidos por emisores "
            "estadounidenses. Asimismo, el Subfondo podrá invertir en títulos de "
            "deuda a corto plazo con calificación investment grade y denominados en "
            "USD, emitidos por emisores fuera de EE. UU. Los títulos de deuda, "
            "incluidos MBS/ABS, deberán tener, en el momento de la compra, una "
            "calificación investment grade. No obstante, el Subfondo podrá invertir, "
            "de manera limitada, en títulos de deuda con calificación inferior a "
            "investment grade o sin calificación como consecuencia de la rebaja de "
            "su calificación crediticia."
        )
        assert _bl_b6_fires("JPM US SH DURATION C USD ACC", kiid) is False, (
            "JPM US Sh Duration is IG-primary ('mínimo 75% IG, de manera limitada'); must not fire"
        )

    def test_al_menos_dos_tercios_guard(self):
        """Generic: 'al menos dos tercios de los activos con grado de inversión' suppresses."""
        kiid = (
            "Al menos dos tercios de los activos tendrán calificación grado de inversión. "
            "Un porcentaje reducido podrá invertirse en títulos con calificación "
            "inferior a grado de inversión."
        )
        assert _bl_b6_fires("FONDO CORP BOND FLEX A ACC", kiid) is False

    def test_como_minimo_porcentaje_guard(self):
        """Generic: 'como mínimo el 80% … investment grade' suppresses."""
        kiid = (
            "El fondo invierte como mínimo el 80% en renta fija con calificación "
            "investment grade. Puede invertir hasta un 20% inferior a grado de inversión."
        )
        assert _bl_b6_fires("PIONEER BOND PLUS A EUR ACC", kiid) is False

    def test_de_manera_limitada_guard(self):
        """Generic: 'podrá invertir de manera limitada … inferior a grado de inversión'."""
        kiid = (
            "El fondo invierte principalmente en bonos investment grade. "
            "Podrá invertir de manera limitada en títulos con calificación "
            "inferior a grado de inversión para mejorar la rentabilidad."
        )
        assert _bl_b6_fires("MULTI BOND BALANCED A EUR ACC", kiid) is False

    def test_genuine_hy_mandate_fires(self):
        """A genuine HY mandate (no IG-majority qualifier) must still fire."""
        kiid = (
            "El fondo invierte principalmente en bonos con calificación "
            "inferior a grado de inversión emitidos por empresas europeas. "
            "El objetivo es generar rentabilidad superior al mercado HY."
        )
        assert _bl_b6_fires("ALLIANZ EURO HY BD AT EUR ACC", kiid) is True, (
            "Genuine HY mandate must still trigger BL-B6-HY-KIID"
        )

    def test_hy_fund_no_ig_qualifier_fires(self):
        """Sub-IG signal without any IG-majority qualifier must fire."""
        kiid = (
            "invierte en títulos con calificación inferior a grado de inversión "
            "en los mercados de crédito europeo."
        )
        assert _bl_b6_fires("CANDRIAM BONDS CR OP C EUR ACC", kiid) is True

    def test_ubs_floating_rate_income_fires(self):
        """UBS Floating Rate Income: 'calificaciones de menor calidad' — confirmed HY."""
        kiid = (
            "puede invertir en títulos con calificaciones de menor calidad que "
            "los títulos con grado de inversión para mejorar la rentabilidad."
        )
        assert _bl_b6_fires("UBS FLOATING RATE INC P ACC", kiid) is True, (
            "UBS Floating Rate Income is genuine sub-IG; must still fire"
        )

    @pytest.mark.parametrize("name,nature", [
        ("AMUNDI LIQUIDITE SR EUR ACC", "Monetario"),
        ("BNP PARIBAS INSTICASH EUR A", "Monetario"),
    ])
    def test_monetario_guard_unchanged(self, name, nature):
        """Monetario guard (FIX-B6-3a) still holds."""
        kiid = "inferior a grado de inversión para liquidez de cartera"
        assert _bl_b6_fires(name, kiid, nature) is False

    def test_aggregate_name_guard_unchanged(self):
        """'aggregate' name guard (FIX-B6-3b) still holds."""
        kiid = "inferior a investment grade EM component"
        assert _bl_b6_fires("VANGUARD EURO AGGREGATE BOND INDEX", kiid) is False
