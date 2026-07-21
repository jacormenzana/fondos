# test_fixes_20260721.py
# R-7: no pipeline.py / core.io imports.
# Covers:
#   FIX-NLC-MSBENCH-1        — Morningstar asset_class as corroboration/arbitration signal
#   FIX-ESTRUCT-NEGATION-1   — Estructurado FP from negated PRIIPs boilerplate
#   FIX-MIXTOS-FOF-1         — multi-asset FoF misclassified as RFC

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from proyecto1.core.classify_utils import (
    resolve_nature_evidence,
    detect_nature_from_kiid,
)

# ── Window-priming helper ─────────────────────────────────────────────────────
# Uses DDF format ("Documento de datos fundamentales" in first 600 chars,
# window = 500-5000). Objective content placed at position 500+ by padding.

_DDF_MARKER = "Documento de datos fundamentales\n"
_DDF_PREFIX  = _DDF_MARKER + " " * 500   # puts objective at char 533+

def _ddf(obj_text: str) -> str:
    """Wrap objective text in a DDF-format KIID so detect_nature_from_kiid
    finds it in the correct window (500-5000)."""
    return _DDF_PREFIX + obj_text + "\n" * 200


# ── Pre-verified KIID objectives ─────────────────────────────────────────────
# detect_nature_from_kiid() return values verified with live classifier.

# Mixtos: "diferentes clases de activos" triggers early Mixtos return.
_MIXTOS_KIID = _ddf(
    "El fondo es un fondo de activos mixto gestionado activamente. "
    "El fondo invierte en diferentes clases de activos incluidas acciones "
    "y valores de renta fija de emisores de todo el mundo."
)

# RFC: bond-only primary mandate
_RFC_KIID = _ddf(
    "El subfondo invierte principalmente en titulos de renta fija y "
    "monetarios a corto plazo de alta calidad crediticia emitidos en euros."
)

# RV: equity-primary
_RV_KIID = _ddf(
    "El subfondo invierte principalmente en acciones de empresas de todo "
    "el mundo con el objetivo de revalorizar el capital a largo plazo."
)


# ═══════════════════════════════════════════════════════════════════════════════
# WS-A: FIX-NLC-MSBENCH-1
# Morningstar asset_class corroborates confidence (no nature change) and is
# eligible for vol-arbitration (P#6-compliant).
# ═══════════════════════════════════════════════════════════════════════════════

def test_ms_mixed_corroborates_mixtos_no_nature_change():
    """MS asset_class='Mixed' corroborates KIID-primary Mixtos.
    Nature must stay Mixtos; confidence must not decrease."""
    # Without MS signal
    nat0, conf0, _ = resolve_nature_evidence(
        "dws invest cons opp",
        _MIXTOS_KIID,
        benchmark_declared="MSCI World Index",   # equity → doesn't corroborate
        ext_asset_class=None,
    )
    # With MS Mixed signal
    nat1, conf1, trace1 = resolve_nature_evidence(
        "dws invest cons opp",
        _MIXTOS_KIID,
        benchmark_declared="MSCI World Index",
        ext_asset_class="Mixed",
    )
    assert nat0 == nat1 == "Mixtos", (
        f"FIX-NLC-MSBENCH-1: nat without MS={nat0}, nat with MS={nat1}; "
        f"both must be Mixtos"
    )
    assert conf1 >= conf0, (
        f"FIX-NLC-MSBENCH-1: confidence must not decrease with corroborating MS signal; "
        f"conf0={conf0} conf1={conf1}"
    )
    assert trace1.get("msbench") == "Mixtos", (
        f"FIX-NLC-MSBENCH-1: trace['msbench'] should be 'Mixtos'; "
        f"got {trace1.get('msbench')!r}"
    )


def test_ms_rate_is_non_informative():
    """MS asset_class='Rate' is non-informative (cash/overnight hurdle) →
    trace msbench=None."""
    _, _, trace = resolve_nature_evidence(
        "jpm income opportunities",
        _RFC_KIID,
        benchmark_declared=None,
        ext_asset_class="Rate",
    )
    assert trace.get("msbench") is None, (
        f"FIX-NLC-MSBENCH-1: 'Rate' must map to None (non-informative); "
        f"got {trace.get('msbench')!r}"
    )


def test_ms_equity_does_not_override_kiid_primary_mixtos():
    """MS asset_class='Equity' must NOT override a KIID-primary Mixtos.
    The MS signal may only corroborate or be an arbitration candidate,
    never a standalone override."""
    nat, conf, trace = resolve_nature_evidence(
        "some balanced fund",
        _MIXTOS_KIID,
        benchmark_declared=None,
        ext_asset_class="Equity",
    )
    assert nat == "Mixtos", (
        f"FIX-NLC-MSBENCH-1: MS Equity must NOT override KIID-primary Mixtos; "
        f"got nat={nat!r}"
    )


def test_ms_fixed_income_corroborates_rfc():
    """MS asset_class='Fixed Income' (→ coarse 'Renta Fija') corroborates an
    RFC primary via _agrees() 'Renta Fija' → RFC/RFF broadening.
    Confidence must rise (corroborators += 1) and nature must stay RFC."""
    nat0, conf0, _ = resolve_nature_evidence(
        "some short bond fund",
        _RFC_KIID,
        benchmark_declared=None,
        ext_asset_class=None,
    )
    nat1, conf1, trace1 = resolve_nature_evidence(
        "some short bond fund",
        _RFC_KIID,
        benchmark_declared=None,
        ext_asset_class="Fixed Income",
    )
    assert nat0 == nat1, (
        f"FIX-NLC-MSBENCH-1: MS Fixed Income must not change RFC nature; "
        f"got {nat0!r}→{nat1!r}"
    )
    assert conf1 > conf0, (
        f"FIX-NLC-MSBENCH-1: corroborating MS Fixed Income must raise confidence; "
        f"got conf0={conf0} conf1={conf1}"
    )
    assert trace1.get("msbench") == "Renta Fija", (
        f"FIX-NLC-MSBENCH-1: trace msbench for 'Fixed Income' should be 'Renta Fija'; "
        f"got {trace1.get('msbench')!r}"
    )


def test_ms_arbitration_candidate_present_in_trace():
    """MS signal populates trace['msbench'] so downstream logging captures it.
    When MS Equity is passed for a Mixtos fund, trace records 'Renta Variable'."""
    _, _, trace = resolve_nature_evidence(
        "some fund",
        _MIXTOS_KIID,
        benchmark_declared=None,
        ext_asset_class="Equity",
    )
    assert trace.get("msbench") == "Renta Variable", (
        f"FIX-NLC-MSBENCH-1: trace['msbench'] should be 'Renta Variable'; "
        f"got {trace.get('msbench')!r}"
    )


def test_ms_mixed_raises_confidence_when_kiid_bench_conflicts():
    """DWS INVEST CONS OPP scenario: KIID benchmark=MSCI World (equity,
    non-corroborating), MS asset_class=Mixed (corroborating) → confidence
    rises compared to MSCI World benchmark only (no MS)."""
    # Baseline: KIID-primary Mixtos + equity KIID benchmark (no corroboration)
    _, conf_no_ms, _ = resolve_nature_evidence(
        "dws invest cons opp",
        _MIXTOS_KIID,
        benchmark_declared="MSCI World Index",
        ext_asset_class=None,
    )
    # With MS Mixed: adds corroboration → confidence rises
    _, conf_with_ms, _ = resolve_nature_evidence(
        "dws invest cons opp",
        _MIXTOS_KIID,
        benchmark_declared="MSCI World Index",
        ext_asset_class="Mixed",
    )
    assert conf_with_ms > conf_no_ms, (
        f"FIX-NLC-MSBENCH-1 (DWS scenario): MS Mixed must raise confidence "
        f"vs equity-benchmark-only; conf_no_ms={conf_no_ms} "
        f"conf_with_ms={conf_with_ms}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# WS-B: FIX-ESTRUCT-NEGATION-1
# Negated PRIIPs boilerplate must NOT trigger Estructurado.
# ═══════════════════════════════════════════════════════════════════════════════

# Real-world PRIIPs boilerplate from ABRDN ST LIQUIDITY and DNCA ALPHA BONDS
_PRIIPS_NEGATION_1 = _ddf(
    "This product does not include any protection from future market performance "
    "or any capital guarantee against credit risk, so you could lose some or "
    "all of your investment. The fund invests primarily in money market instruments."
)

_PRIIPS_NEGATION_2 = _ddf(
    "The product is intended for investors who can withstand capital losses "
    "and who do not require a capital guarantee. Other information: the "
    "depositary is BNP Paribas, Luxembourg branch. "
    "The fund invests across multiple asset classes including equities and bonds."
)


def test_negated_capital_guarantee_does_not_trigger_estructurado():
    """'does not include … any capital guarantee' is standard PRIIPs boilerplate
    in non-structured funds → must NOT return Estructurado."""
    result = detect_nature_from_kiid(_PRIIPS_NEGATION_1)
    assert result != "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1: negated 'capital guarantee' must not → Estructurado; "
        f"got {result!r}"
    )


def test_do_not_require_capital_guarantee_does_not_trigger_estructurado():
    """'do not require a capital guarantee' is standard PRIIPs boilerplate
    in non-structured funds → must NOT return Estructurado."""
    result = detect_nature_from_kiid(_PRIIPS_NEGATION_2)
    assert result != "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1: 'do not require a capital guarantee' must not "
        f"→ Estructurado; got {result!r}"
    )


def test_genuine_autocall_still_triggers_estructurado():
    """A fund whose objective contains 'autocall' is a genuine structured product
    → must still return Estructurado (regression guard)."""
    kiid = _ddf(
        "The Fund aims to provide capital appreciation and participates in the "
        "performance of the underlying index via an autocall mechanism that "
        "terminates early if the index hits a predetermined barrier level."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1 regression: genuine autocall fund must → Estructurado; "
        f"got {result!r}"
    )


def test_genuine_capital_guarantee_in_objective_still_triggers_estructurado():
    """A fund that AFFIRMATIVELY guarantees capital → still Estructurado."""
    kiid = _ddf(
        "The Fund provides investors with a capital guarantee at maturity through "
        "a CPPI mechanism, ensuring the return of at least 90% of invested capital "
        "at the end of the recommended five-year holding period."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1 regression: affirmative capital guarantee must "
        f"→ Estructurado; got {result!r}"
    )


def test_structured_note_in_objective_triggers_estructurado():
    """'structured note' in objective → Estructurado (regression)."""
    kiid = _ddf(
        "The Fund invests in a structured note linked to the performance of the "
        "MSCI World index, with knock-in protection at 60% of the initial level."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1 regression: structured note must → Estructurado; "
        f"got {result!r}"
    )


def test_knock_in_barrier_triggers_estructurado():
    """'knock-in' and 'barrier' → Estructurado (regression)."""
    kiid = _ddf(
        "El fondo proporciona exposicion al indice subyacente con proteccion "
        "knock-in al 65% del nivel inicial. El mecanismo barrier protege el "
        "capital en caso de caida moderada."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Estructurado", (
        f"FIX-ESTRUCT-NEGATION-1 regression: knock-in/barrier must → Estructurado; "
        f"got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# WS-B: FIX-MIXTOS-FOF-1
# Multi-asset fund-of-funds objective → Mixtos (not _RF_pending / RFC).
# ═══════════════════════════════════════════════════════════════════════════════

# Real DWS MULTI OPP (LU1673812605) objective text (accented chars removed for CI)
_DWS_MULTIOPP_OBJECTIVE = _ddf(
    "El objetivo de la politica de inversion consiste en obtener la mayor "
    "revalorizacion posible en euros. Para lograr este objetivo, el fondo "
    "invierte un minimo del 25% en acciones de fondos de renta variable, "
    "fondos mixtos de valores, fondos de renta fija y fondos monetarios de "
    "corto plazo, tanto nacionales como extranjeros. Adicionalmente, puede "
    "invertir en acciones, valores de renta fija, certificados de acciones, "
    "obligaciones y bonos convertibles."
)

# Pure RF objective (regression: must NOT become Mixtos)
_PURE_RFC_OBJECTIVE = _ddf(
    "El subfondo invierte principalmente en titulos de renta fija y en "
    "instrumentos del mercado monetario a corto plazo emitidos en euros. "
    "El mandato es exclusivamente de renta fija."
)


def test_multiopp_fof_objective_returns_mixtos():
    """DWS MULTI OPP-style objective: FoF with fondos mixtos de valores +
    fondos de renta variable + fondos de renta fija → Mixtos (not _RF_pending)."""
    result = detect_nature_from_kiid(_DWS_MULTIOPP_OBJECTIVE)
    assert result == "Mixtos", (
        f"FIX-MIXTOS-FOF-1: multi-asset FoF objective must → 'Mixtos'; "
        f"got {result!r}"
    )


def test_pure_rf_objective_not_affected_by_fof_fix():
    """Regression: a pure RF objective (no FoF fund-type enumeration) must
    NOT be affected by FIX-MIXTOS-FOF-1."""
    result = detect_nature_from_kiid(_PURE_RFC_OBJECTIVE)
    assert result != "Mixtos", (
        f"FIX-MIXTOS-FOF-1 regression: pure RF objective must not → Mixtos; "
        f"got {result!r}"
    )


def test_fondos_mixtos_explicit_triggers_mixtos():
    """'fondos mixtos' alone in objective window → Mixtos."""
    kiid = _ddf(
        "El fondo invierte en una cartera diversificada de fondos mixtos "
        "de valores para obtener rentabilidades consistentes a largo plazo."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Mixtos", (
        f"FIX-MIXTOS-FOF-1: 'fondos mixtos' alone should → Mixtos; "
        f"got {result!r}"
    )


def test_two_fof_asset_classes_rv_and_rf_triggers_mixtos():
    """'fondos de renta variable' + 'fondos de renta fija' (≥2 FoF types)
    in objective window → Mixtos."""
    kiid = _ddf(
        "El fondo invierte principalmente en fondos de renta variable y fondos "
        "de renta fija, con el fin de alcanzar el crecimiento del capital "
        "preservando al mismo tiempo un nivel razonable de estabilidad."
    )
    result = detect_nature_from_kiid(kiid)
    assert result == "Mixtos", (
        f"FIX-MIXTOS-FOF-1: two FoF asset-class types (RV+RF) should → Mixtos; "
        f"got {result!r}"
    )
