# proyecto1/tests/test_cost_cross_validator.py
# -*- coding: utf-8 -*-
"""
Tests para cost_cross_validator.py — BL-COST-5.

Ejecutar:
    python -X utf8 test_cost_cross_validator.py

Nota sobre T3 (test 3):
    El traspaso anota "discrepancy_bp≈500 (error grave)" y "validated_pct=None"
    para validate_pct_eur(0.005, 100.0, 10000).
    El cálculo real es: implied=100/10000=0.01, diff=|0.005-0.01|=0.005=50bp.
    50bp cae exactamente en el umbral leve (diff <= 0.005) → DISCREPANCY leve,
    validated_pct=0.005 (no None). El comentario del traspaso tenía un error
    aritmético (confundió 50bp con 500bp). El test verifica la lógica correcta.
"""

import sys
import os

# ---------------------------------------------------------------------------
# Resolver import
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.normpath(os.path.join(_THIS_DIR, '..', 'core'))
for _p in (_CORE_DIR, _THIS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from cost_cross_validator import validate_pct_eur, ValidationResult
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cost_cross_validator import validate_pct_eur, ValidationResult


# ======================================================================
# Helpers
# ======================================================================

def _bp(diff_ratio: float) -> float:
    """Convierte diferencia en ratio a basis points."""
    return diff_ratio * 10000


# ======================================================================
# Tests del traspaso (T1–T8)
# ======================================================================

def test_t1_ok_exact():
    """
    T1: validate_pct_eur(0.005, 50.0, 10000)
    implied=0.005, diff=0bp → OK, validated_pct=0.005, discrepancy_bp≈0.
    """
    r = validate_pct_eur(0.005, 50.0, 10000)
    assert r.status == 'OK', f"Expected OK, got {r.status!r}. notes={r.notes}"
    assert r.validated_pct == 0.005, f"validated_pct={r.validated_pct}"
    assert r.discrepancy_bp is not None
    assert abs(r.discrepancy_bp) < 0.1, f"discrepancy_bp={r.discrepancy_bp} (esperado ≈0)"


def test_t2_ok_within_tolerance():
    """
    T2: validate_pct_eur(0.005, 52.0, 10000)
    implied=0.0052, diff=2bp — dentro de la tolerancia de 5bp → OK.
    (El traspaso anotó DISCREPANCY pero 2bp < 5bp → OK según la lógica.)
    """
    r = validate_pct_eur(0.005, 52.0, 10000)
    # diff = |0.005 - 0.0052| = 0.0002 = 2bp < tolerance(5bp) → OK
    assert r.status == 'OK', f"Expected OK (2bp < 5bp tolerance), got {r.status!r}. notes={r.notes}"
    assert r.validated_pct == 0.005
    assert r.discrepancy_bp is not None
    assert abs(r.discrepancy_bp - 2.0) < 0.1, f"discrepancy_bp={r.discrepancy_bp}"


def test_t3_discrepancy_50bp_boundary():
    """
    T3: validate_pct_eur(0.005, 100.0, 10000)
    implied=0.01, diff=50bp — exactamente en el umbral leve.
    diff <= 0.005 → DISCREPANCY leve, validated_pct=0.005 (no None).

    NOTA: el traspaso anotaba validated_pct=None (error aritmético: confundía
    50bp con 500bp). La lógica correcta: 50bp cae en rama leve → validated_pct=pct.
    """
    r = validate_pct_eur(0.005, 100.0, 10000)
    assert r.status == 'DISCREPANCY', f"Expected DISCREPANCY, got {r.status!r}"
    assert r.validated_pct == 0.005, (
        f"validated_pct={r.validated_pct} — 50bp es leve (diff<=0.005), "
        f"se debe confiar en pct"
    )
    assert r.discrepancy_bp is not None
    assert abs(r.discrepancy_bp - 50.0) < 0.1, f"discrepancy_bp={r.discrepancy_bp}"


def test_t4_pct_only():
    """
    T4: validate_pct_eur(0.005, None, 10000)
    → PCT_ONLY, validated_pct=0.005.
    """
    r = validate_pct_eur(0.005, None, 10000)
    assert r.status == 'PCT_ONLY', f"Expected PCT_ONLY, got {r.status!r}"
    assert r.validated_pct == 0.005
    assert r.discrepancy_bp is None


def test_t5_eur_only():
    """
    T5: validate_pct_eur(None, 50.0, 10000)
    → EUR_ONLY, validated_pct=0.005 (50/10000).
    """
    r = validate_pct_eur(None, 50.0, 10000)
    assert r.status == 'EUR_ONLY', f"Expected EUR_ONLY, got {r.status!r}"
    assert r.validated_pct is not None
    assert abs(r.validated_pct - 0.005) < 1e-9, f"validated_pct={r.validated_pct}"
    assert r.discrepancy_bp is None


def test_t6_none():
    """
    T6: validate_pct_eur(None, None, 10000)
    → NONE, validated_pct=None.
    """
    r = validate_pct_eur(None, None, 10000)
    assert r.status == 'NONE', f"Expected NONE, got {r.status!r}"
    assert r.validated_pct is None
    assert r.discrepancy_bp is None


def test_t7_fidelity_usd():
    """
    T7: validate_pct_eur(0.0525, 510.0, 10000) — LU1084165304 (USD base 10.000)
    implied=0.051, diff=15bp — dentro del rango leve (5bp < 15bp <= 50bp)
    → DISCREPANCY leve, validated_pct=0.0525.
    """
    r = validate_pct_eur(0.0525, 510.0, 10000)
    # diff = |0.0525 - 0.051| = 0.0015 = 15bp
    assert r.status == 'DISCREPANCY', f"Expected DISCREPANCY (15bp), got {r.status!r}"
    assert r.validated_pct == 0.0525, f"validated_pct={r.validated_pct} (debe confiar en %)"
    assert r.discrepancy_bp is not None
    assert abs(r.discrepancy_bp - 15.0) < 0.1, f"discrepancy_bp={r.discrepancy_bp}"


def test_t8_polar_capital_rounding():
    """
    T8: validate_pct_eur(0.001, 12.0, 10000) — IE00BZ4D7085 (monetario, redondeo)
    implied=0.0012, diff=2bp — dentro de la tolerancia de 5bp → OK.
    validated_pct=0.001 (confiamos en %).

    NOTA: el traspaso anotó DISCREPANCY pero 2bp < 5bp tolerance → OK.
    """
    r = validate_pct_eur(0.001, 12.0, 10000)
    # diff = |0.001 - 0.0012| = 0.0002 = 2bp < 5bp → OK
    assert r.status == 'OK', f"Expected OK (2bp < 5bp tolerance), got {r.status!r}"
    assert r.validated_pct == 0.001, f"validated_pct={r.validated_pct}"
    assert r.discrepancy_bp is not None
    assert abs(r.discrepancy_bp - 2.0) < 0.1, f"discrepancy_bp={r.discrepancy_bp}"


# ======================================================================
# Tests adicionales
# ======================================================================

def test_grave_above_50bp():
    """
    Discrepancia grave: diff > 50bp → validated_pct=None.
    validate_pct_eur(0.005, 200.0, 10000): implied=0.02, diff=150bp.
    """
    r = validate_pct_eur(0.005, 200.0, 10000)
    assert r.status == 'DISCREPANCY', f"Expected DISCREPANCY, got {r.status!r}"
    assert r.validated_pct is None, (
        f"validated_pct debe ser None para diff>50bp, got {r.validated_pct}"
    )
    assert r.discrepancy_bp is not None
    assert r.discrepancy_bp > 50.0, f"discrepancy_bp={r.discrepancy_bp}"


def test_result_is_dataclass():
    """El resultado es una instancia de ValidationResult."""
    r = validate_pct_eur(0.01, 100.0, 10000)
    assert isinstance(r, ValidationResult)
    assert hasattr(r, 'status')
    assert hasattr(r, 'validated_pct')
    assert hasattr(r, 'discrepancy_bp')
    assert hasattr(r, 'notes')


def test_notes_nonempty():
    """El campo notes siempre tiene contenido."""
    for args in [
        (0.005, 50.0),
        (0.005, None),
        (None, 50.0),
        (None, None),
    ]:
        r = validate_pct_eur(*args, 10000)
        assert r.notes, f"notes vacío para args={args}"


def test_custom_base():
    """La base personalizada se aplica correctamente."""
    # base=1000: implied = 50/1000 = 0.05, diff=0 → OK
    r = validate_pct_eur(0.05, 50.0, base=1000.0)
    assert r.status == 'OK', f"Expected OK with base=1000, got {r.status!r}"
    assert r.validated_pct == 0.05


def test_custom_tolerance():
    """La tolerancia personalizada se respeta."""
    # diff=2bp; con tolerance=1bp debe ser DISCREPANCY
    r = validate_pct_eur(0.005, 52.0, 10000, tolerance=0.0001)
    assert r.status == 'DISCREPANCY', (
        f"Con tolerance=1bp y diff=2bp debe ser DISCREPANCY, got {r.status!r}"
    )

def test_zero_cost_ok():
    """Coste cero: pct=0.0 y EUR=0.0 → OK, discrepancy_bp=0."""
    r = validate_pct_eur(0.0, 0.0, 10000)
    assert r.status == 'OK', f"Expected OK for zero cost, got {r.status!r}"
    assert r.validated_pct == 0.0
    assert r.discrepancy_bp == 0.0


def test_eur_only_computes_implied_correctly():
    """EUR_ONLY: implied_pct = eur/base con precisión."""
    r = validate_pct_eur(None, 175.0, 10000)
    assert r.status == 'EUR_ONLY'
    expected = 175.0 / 10000.0
    assert abs(r.validated_pct - expected) < 1e-9, (
        f"implied_pct={r.validated_pct}, expected={expected}"
    )


# ======================================================================
# Runner
# ======================================================================

def _run_all_tests() -> None:
    suite = [
        # Tests obligatorios del traspaso (T1–T8)
        test_t1_ok_exact,
        test_t2_ok_within_tolerance,
        test_t3_discrepancy_50bp_boundary,
        test_t4_pct_only,
        test_t5_eur_only,
        test_t6_none,
        test_t7_fidelity_usd,
        test_t8_polar_capital_rounding,
        # Tests adicionales
        test_grave_above_50bp,
        test_result_is_dataclass,
        test_notes_nonempty,
        test_custom_base,
        test_custom_tolerance,
        test_zero_cost_ok,
        test_eur_only_computes_implied_correctly,
    ]

    passed = 0
    failed = []
    for fn in suite:
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed.append(f'  FAIL {fn.__name__}: {exc}')
        except Exception as exc:
            failed.append(f'  ERROR {fn.__name__}: {exc}')

    print(f'Suite: {passed}/{len(suite)} OK')
    if failed:
        print('\n'.join(failed))
        sys.exit(1)


if __name__ == '__main__':
    _run_all_tests()
