# proyecto1/core/cost_cross_validator.py
# -*- coding: utf-8 -*-
"""
cost_cross_validator.py — cross-validación porcentaje ↔ importe EUR en costes PRIIPs.

BL-COST-5 (Sprint 2 S2-A): módulo nuevo.

Propósito:
    Validar la coherencia entre el porcentaje declarado y el importe EUR absoluto
    de la tabla de costes PRIIPs. El estándar PRIIPs obliga a publicar ambos valores;
    la discrepancia entre ellos es señal de error de extracción o de redondeo.

    Reutilizado por:
        - priips_cost_extractor.py (S2-B) para decidir qué valor usar
        - Futuras reglas INTER-COST (BL-COST-5)

Constantes importadas de shared.config:
    PRIIPS_INVESTMENT_BASE              = 10000.0
    COST_CROSS_VALIDATION_TOLERANCE_PCT = 0.0005  (5bp — corregido en §1 S2-A)

Lógica de clasificación (umbrales en ratio decimal):
    diff = |declared_pct - implied_pct|  donde implied_pct = eur_amount / base

    diff <= 0.0005  (5bp)  → OK          — dentro de tolerancia de redondeo
    diff <= 0.005  (50bp)  → DISCREPANCY — discrepancia leve; confiar en %
    diff >  0.005  (50bp)  → DISCREPANCY — discrepancia grave; validated_pct=None

Función exportada:
    validate_pct_eur(pct, eur_amount, base, tolerance) -> ValidationResult
"""

import sys
import os
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Import de constantes desde shared.config
# Fallback a valores hardcoded si el import falla (entorno de test aislado).
# ---------------------------------------------------------------------------
_SHARED_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'shared')
)
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

try:
    from config import PRIIPS_INVESTMENT_BASE, COST_CROSS_VALIDATION_TOLERANCE_PCT
except ImportError:
    # Valores por defecto para entornos sin shared/config.py accesible
    PRIIPS_INVESTMENT_BASE: float = 10000.0
    COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005  # 5bp


# ======================================================================
# Umbral de discrepancia grave
# ======================================================================

# Por encima de 50bp la discrepancia entre % y EUR es tan grande que
# no podemos confiar en ninguno de los dos valores sin revisión manual.
# validated_pct queda None para indicar que el dato requiere revisión.
_SEVERE_DISCREPANCY_THRESHOLD: float = 0.005  # 50 basis points


# ======================================================================
# Dataclass de resultado
# ======================================================================

@dataclass
class ValidationResult:
    """
    Resultado de la cross-validación porcentaje ↔ EUR.

    Atributos:
        status:         'OK' | 'DISCREPANCY' | 'PCT_ONLY' | 'EUR_ONLY' | 'NONE'
        validated_pct:  el valor más fiable como ratio decimal (ej: 0.005 = 0.5%).
                        None si la discrepancia es grave o si no hay datos.
        discrepancy_bp: diferencia entre pct declarado e implied_pct, en basis points.
                        None cuando no aplica (PCT_ONLY, EUR_ONLY, NONE).
        notes:          descripción legible del resultado para logging.
    """
    status:         str
    validated_pct:  Optional[float]
    discrepancy_bp: Optional[float]
    notes:          str


# ======================================================================
# API pública
# ======================================================================

def validate_pct_eur(
    pct:        Optional[float],
    eur_amount: Optional[float],
    base:       float = PRIIPS_INVESTMENT_BASE,
    tolerance:  float = COST_CROSS_VALIDATION_TOLERANCE_PCT,
) -> ValidationResult:
    """
    Cross-valida porcentaje declarado vs importe EUR absoluto de un coste PRIIPs.

    Fórmula:
        implied_pct = eur_amount / base
        diff        = abs(pct - implied_pct)
        diff_bp     = diff * 10000

    Criterios (aplicados en orden):
        1. Ambos None          → NONE,          validated_pct=None
        2. Solo pct            → PCT_ONLY,       validated_pct=pct
        3. Solo eur_amount     → EUR_ONLY,       validated_pct=implied_pct
        4. Ambos disponibles:
           diff <= tolerance   → OK,             validated_pct=pct
           diff <= 50bp        → DISCREPANCY,    validated_pct=pct   (redondeo tolerable)
           diff >  50bp        → DISCREPANCY,    validated_pct=None  (error grave)

    Args:
        pct:        porcentaje declarado como ratio decimal (ej: 0.005 para 0.5%).
                    None si no se extrajo.
        eur_amount: importe absoluto en la moneda base del KID (EUR, USD, etc.).
                    None si no se extrajo.
        base:       base de inversión estándar PRIIPs (default: 10000.0).
        tolerance:  umbral de tolerancia en ratio decimal (default: 0.0005 = 5bp).

    Returns:
        ValidationResult con status, validated_pct, discrepancy_bp y notes.
    """
    # ── Caso NONE: ningún dato disponible ────────────────────────────────
    if pct is None and eur_amount is None:
        return ValidationResult(
            status='NONE',
            validated_pct=None,
            discrepancy_bp=None,
            notes='No se dispone de porcentaje ni de importe EUR.',
        )

    # ── Caso PCT_ONLY: solo porcentaje ───────────────────────────────────
    if eur_amount is None:
        return ValidationResult(
            status='PCT_ONLY',
            validated_pct=pct,
            discrepancy_bp=None,
            notes=f'Solo porcentaje disponible: {pct:.6f} ({pct * 100:.4f}%). '
                  f'No hay importe EUR para cross-validar.',
        )

    # ── Caso EUR_ONLY: solo importe EUR ──────────────────────────────────
    if pct is None:
        implied = eur_amount / base
        return ValidationResult(
            status='EUR_ONLY',
            validated_pct=round(implied, 6),
            discrepancy_bp=None,
            notes=f'Solo importe EUR disponible: {eur_amount} / {base} = '
                  f'{implied:.6f} ({implied * 100:.4f}%). '
                  f'No hay porcentaje declarado para cross-validar.',
        )

    # ── Ambos disponibles: cross-validación ──────────────────────────────
    implied = eur_amount / base
    diff    = abs(pct - implied)
    diff_bp = round(diff * 10000, 2)

    if diff <= tolerance:
        return ValidationResult(
            status='OK',
            validated_pct=pct,
            discrepancy_bp=diff_bp,
            notes=f'Cross-validación OK. pct={pct:.6f}, implied={implied:.6f}, '
                  f'diff={diff_bp:.2f}bp (≤ {tolerance * 10000:.1f}bp tolerancia).',
        )

    if diff <= _SEVERE_DISCREPANCY_THRESHOLD:
        return ValidationResult(
            status='DISCREPANCY',
            validated_pct=pct,
            discrepancy_bp=diff_bp,
            notes=f'Discrepancia leve: pct={pct:.6f}, implied={implied:.6f}, '
                  f'diff={diff_bp:.2f}bp. Se confía en el porcentaje declarado.',
        )

    # Discrepancia grave: > 50bp
    return ValidationResult(
        status='DISCREPANCY',
        validated_pct=None,
        discrepancy_bp=diff_bp,
        notes=f'Discrepancia grave (>{_SEVERE_DISCREPANCY_THRESHOLD * 10000:.0f}bp): '
              f'pct={pct:.6f} vs implied={implied:.6f} (diff={diff_bp:.2f}bp). '
              f'Requiere revisión manual. validated_pct=None.',
    )
