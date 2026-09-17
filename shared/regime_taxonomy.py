# shared/regime_taxonomy.py
# -*- coding: utf-8 -*-
"""
Vocabulario canonico de regimenes macroeconomicos (P3 optimization plan,
Phase 1g).

Fuente unica de verdad (P#11 / R-1) para las 7 etiquetas de regimen, su
sufijo de columna en fund_metrics y los pesos de sub-cartera por regimen.
Antes de este modulo, REGIMES/_REGIME_SUFFIX/REGIME_WEIGHTS estaban
definidos en proyecto3/src/regime_classifier.py (P3) y _REGIME_SUFFIX se
re-declaraba a mano, de forma independiente, en
proyecto2/src/calculations/regime_returns.py (P2) -- los dos coincidian por
disciplina, no por construccion; catalog_invariants.py (shared) tambien
hand-listaba el mismo conjunto de 7 regimenes en minusculas para generar sus
reglas N_OBS_NONNEG_*, con un comentario admitiendo que ya se habia
equivocado una vez.

Vive en shared/ (no en proyecto3/) porque P2's regime_returns.py ya
dependia de estos datos -- mantenerlos en proyecto3 era precisamente lo que
forzaba un import invertido P2->P3 (regime_returns.py importando
RegimeClassifier desde proyecto3.src, contrario al flujo documentado
P1 -> P2 -> P3 en AGENTS.md).

Uso:
    from shared.regime_taxonomy import REGIMES, REGIME_SUFFIX, REGIME_WEIGHTS
"""

# Regimenes en orden de prioridad de clasificacion (ver
# proyecto3/src/regime_classifier.py::_classify_row para el arbol de
# decision que produce estas etiquetas).
REGIMES: tuple[str, ...] = (
    "Crisis_Financiera",
    "Shock_Energetico",
    "Estanflacion",
    "Contraccion",
    "Recalentamiento_Tardio",
    "Recalentamiento",
    "Expansion",
)

# Sufijo usado en nombres de columna de fund_metrics (ej. return_ann_expansion).
# Derivado de REGIMES: nombre de regimen en minusculas (guiones bajos intactos).
REGIME_SUFFIX: dict[str, str] = {r: r.lower() for r in REGIMES}

# Pesos de sub-cartera por regimen: (Defensiva, Equilibrada, Dinamica).
REGIME_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "Crisis_Financiera":      (0.70, 0.25, 0.05),
    "Shock_Energetico":       (0.55, 0.35, 0.10),
    "Estanflacion":           (0.50, 0.35, 0.15),
    "Contraccion":            (0.60, 0.35, 0.05),
    "Recalentamiento_Tardio": (0.40, 0.40, 0.20),
    "Recalentamiento":        (0.30, 0.40, 0.30),
    "Expansion":              (0.20, 0.45, 0.35),
}
