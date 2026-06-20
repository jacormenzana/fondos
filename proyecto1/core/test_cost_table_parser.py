# proyecto1/tests/test_cost_table_parser.py
# -*- coding: utf-8 -*-
"""
Tests para cost_table_parser.py — BL-COST-4.

Ejecutar:
    python -X utf8 test_cost_table_parser.py
"""

import sys
import os

# ---------------------------------------------------------------------------
# Resolver import
# ---------------------------------------------------------------------------
_THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_THIS_DIR, '..', 'core'))
_ROOT_DIR  = os.path.normpath(os.path.join(_THIS_DIR, '..', '..'))

for _p in (_CORE_DIR, _THIS_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from cost_table_parser import (
        parse_costs_over_time,
        parse_costs_composition,
        _normalize_amount,
        _parse_horizon_years,
        _is_rhp_label,
    )
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cost_table_parser import (
        parse_costs_over_time,
        parse_costs_composition,
        _normalize_amount,
        _parse_horizon_years,
        _is_rhp_label,
    )


# ======================================================================
# Textos DLA2 sintéticos — fieles al formato real de serialización
# ======================================================================

# IE00BZ4D7085 (Polar Capital) — monetario, RHP=1Y, coste muy bajo
# Costes over time: 1 columna (RHP=1 año), total=12 EUR, ACI=0.12%
# Composición: entry=0%, entry_max=5%, management≈0.10%, transaction≈0.02%
DLA2_BZ4D7085 = """
Información adicional

|||Costes a lo largo del tiempo|||
|||Inversión: 10.000 EUR|||En caso de salida después del período de mantenimiento recomendado (1 año)|||
|||Costes totales|||12 EUR|||
|||Incidencia anual de los costes (*)|||0,12%|||

|||Composición de los costes|||
|||Costes de entrada|||0% del importe que usted paga cuando realiza esta inversión. Este es el máximo que se le cobrará.|||
|||Costes de salida|||0% del importe que usted recibe al salir de la inversión.|||
|||Comisiones de gestión y otros costes administrativos y de explotación|||0,10% del valor de su inversión al año. Se trata de una estimación basada en los costes reales del pasado.|||
|||Costes de operación de la cartera|||0,02% del valor de su inversión al año. Se trata de una estimación basada en los costes reales del pasado.|||

Texto adicional. Hasta el 5% del importe de entrada podría aplicarse en el futuro.
"""

# IE00B45H7020 (BlackRock) — 2 columnas: 1Y y RHP(1Y)
DLA2_B45H7020 = """
|||Costes a lo largo del tiempo|||
|||Inversión: 10.000 EUR|||En caso de salida después de 1 año|||En caso de salida después del período de mantenimiento recomendado (1 año)|||
|||Costes totales|||10 EUR|||10 EUR|||
|||Incidencia anual de los costes (*)|||0,10%|||0,10%|||

|||Composición de los costes|||
|||Costes de entrada|||0% del importe que paga.|||
|||Costes de salida|||0%|||
|||Comisiones de gestión y otros costes|||0,10% al año.|||
|||Costes de operación de la cartera|||0,00%|||
"""

# LU1502282632 (Candriam) — entry max 3.50%
DLA2_LU1502282632 = """
|||Costes a lo largo del tiempo|||
|||Inversión: 10.000 EUR|||En caso de salida después de 1 año|||En caso de salida después de 5 años|||
|||Costes totales|||180 EUR|||1.100 EUR|||
|||Incidencia anual de los costes (*)|||1,80%|||1,82%|||

|||Composición de los costes|||
|||Costes de entrada|||Hasta el 3,50% del importe que usted paga.|||
|||Costes de salida|||0%|||
|||Comisiones de gestión y otros costes|||1,75% del valor de su inversión al año.|||
|||Costes de operación de la cartera|||0,05%|||
"""

# FR0000989626 (Groupama) — entry 0.50% / 50 EUR exacto
DLA2_FR0000989626 = """
|||Costes a lo largo del tiempo|||
|||Inversión: 10.000 EUR|||En caso de salida después de 1 año|||En caso de salida después de 5 años|||
|||Costes totales|||170 EUR|||1.050 EUR|||
|||Incidencia anual de los costes (*)|||1,70%|||1,71%|||

|||Composición de los costes|||
|||Costes de entrada|||0,50% del importe que usted paga. Esto es lo máximo que se le puede cobrar.|||50 EUR|||
|||Costes de salida|||0%|||0 EUR|||
|||Comisiones de gestión y otros costes|||1,20% al año.|||
|||Costes de operación de la cartera|||0,05%|||
"""

# Texto plano (sin DLA2) — ejemplo genérico
PLAIN_PRIIPS = """
Costes a lo largo del tiempo

Si sale después de 1 año: 150 EUR de costes, incidencia anual de los costes 1,5%.
Si sale después de 5 años: los costes serán de 820 EUR (1,6% anual).

Composición de los costes
Costes de entrada: 0% del importe que usted paga.
Costes de salida: 0%.
Comisiones de gestión: 1,5% del valor de su inversión al año.
Costes de operación de la cartera: 0,05%.
"""

# ======================================================================
# Tests: _normalize_amount
# ======================================================================

def test_normalize_european_thousands():
    """Test 5: "1.360" → 1360.0 (punto como separador de miles europeo)."""
    result = _normalize_amount("1.360")
    assert result == 1360.0, f"Expected 1360.0, got {result}"

def test_normalize_european_decimal():
    """Test 6: "1,5" → 1.5 (coma como separador decimal europeo)."""
    result = _normalize_amount("1,5")
    assert result == 1.5, f"Expected 1.5, got {result}"

def test_normalize_integer():
    """Test 7: "153" → 153.0."""
    result = _normalize_amount("153")
    assert result == 153.0, f"Expected 153.0, got {result}"

def test_normalize_anglo_decimal():
    """Punto decimal anglosajón "1.5" → 1.5 (no 1500)."""
    result = _normalize_amount("1.5")
    assert result == 1.5, f"Expected 1.5, got {result}"

def test_normalize_both_separators_eu():
    """"1.360,00" (europeo) → 1360.0."""
    result = _normalize_amount("1.360,00")
    assert result == 1360.0, f"Expected 1360.0, got {result}"

def test_normalize_both_separators_en():
    """"1,360.00" (anglosajón) → 1360.0."""
    result = _normalize_amount("1,360.00")
    assert result == 1360.0, f"Expected 1360.0, got {result}"

def test_normalize_none_on_empty():
    """Cadena vacía → None."""
    assert _normalize_amount("") is None
    assert _normalize_amount(None) is None  # type: ignore[arg-type]

def test_normalize_zero():
    """"0" → 0.0."""
    result = _normalize_amount("0")
    assert result == 0.0, f"Expected 0.0, got {result}"


# ======================================================================
# Tests: _parse_horizon_years y _is_rhp_label
# ======================================================================

def test_parse_horizon_1_year_es():
    """Test 8: "1 año" → 1.0."""
    assert _parse_horizon_years("1 año") == 1.0

def test_parse_horizon_rhp():
    """Test 9: RHP → -1.0."""
    assert _parse_horizon_years("período de mantenimiento recomendado") == -1.0
    assert _parse_horizon_years("recommended holding period") == -1.0

def test_parse_horizon_3_months():
    """Test 10: "3 meses" → 0.25."""
    result = _parse_horizon_years("3 meses")
    assert abs(result - 0.25) < 0.001, f"Expected 0.25, got {result}"

def test_parse_horizon_5_years():
    """5 años → 5.0."""
    assert _parse_horizon_years("5 años") == 5.0
    assert _parse_horizon_years("5 years") == 5.0

def test_parse_horizon_6_months():
    """6 meses → 0.5."""
    result = _parse_horizon_years("6 meses")
    assert abs(result - 0.5) < 0.001, f"Expected 0.5, got {result}"

def test_parse_horizon_1_month():
    """1 mes → ~0.083."""
    result = _parse_horizon_years("1 mes")
    assert abs(result - 0.0833) < 0.001, f"Expected ~0.083, got {result}"

def test_is_rhp_label_true():
    """Etiquetas RHP → True."""
    assert _is_rhp_label("período de mantenimiento recomendado") is True
    assert _is_rhp_label("Recommended Holding Period") is True
    assert _is_rhp_label("En caso de salida después del período de mantenimiento recomendado (1 año)") is True

def test_is_rhp_label_false():
    """Etiquetas no RHP → False."""
    assert _is_rhp_label("1 año") is False
    assert _is_rhp_label("5 years") is False


# ======================================================================
# Tests: parse_costs_over_time
# ======================================================================

def test_costs_over_time_bz4d7085_rhp():
    """
    Test 1: IE00BZ4D7085 — 1 fila (RHP=1Y).
    horizon_years=-1.0, is_rhp=True, total_cost_eur=12.0, aci_pct≈0.0012.
    """
    rows = parse_costs_over_time(DLA2_BZ4D7085)
    assert len(rows) >= 1, f"Expected >=1 row, got {len(rows)}: {rows}"

    rhp_rows = [r for r in rows if r['is_rhp']]
    assert len(rhp_rows) >= 1, f"No RHP row found. rows={rows}"

    row = rhp_rows[0]
    assert row['horizon_years'] == -1.0, f"horizon_years={row['horizon_years']}"
    assert row['is_rhp'] is True
    assert row['total_cost_eur'] == 12.0, f"total_cost_eur={row['total_cost_eur']}"
    assert row['aci_pct'] is not None
    assert abs(row['aci_pct'] - 0.0012) < 0.00005, f"aci_pct={row['aci_pct']}"
    assert row['source'] == 'DLA2'

def test_costs_over_time_b45h7020_two_columns():
    """
    Test 2: IE00B45H7020 — 2 columnas (1Y y RHP).
    Debe retornar 2 filas: una con horizon_years=1.0 y una con is_rhp=True.
    """
    rows = parse_costs_over_time(DLA2_B45H7020)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"

    years_set = {r['horizon_years'] for r in rows}
    has_1y  = any(r['horizon_years'] == 1.0 for r in rows)
    has_rhp = any(r['is_rhp'] for r in rows)
    assert has_1y,  f"No 1-year row. years_set={years_set}"
    assert has_rhp, f"No RHP row. years_set={years_set}"

    for row in rows:
        assert row['total_cost_eur'] == 10.0, f"total_cost_eur={row['total_cost_eur']}"
        assert row['aci_pct'] is not None
        assert abs(row['aci_pct'] - 0.001) < 0.00005, f"aci_pct={row['aci_pct']}"

def test_costs_over_time_lu1502282632_two_horizons():
    """LU1502282632 — 2 columnas (1Y y 5Y)."""
    rows = parse_costs_over_time(DLA2_LU1502282632)
    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
    years = sorted(r['horizon_years'] for r in rows)
    assert years == [1.0, 5.0], f"Expected [1.0, 5.0], got {years}"

def test_costs_over_time_empty_text():
    """Texto vacío → lista vacía."""
    assert parse_costs_over_time('') == []
    assert parse_costs_over_time(None) == []  # type: ignore[arg-type]

def test_costs_over_time_no_table():
    """Texto sin tabla de costes → lista vacía."""
    result = parse_costs_over_time("Texto genérico sin tabla de costes.")
    assert result == []

def test_costs_over_time_plain_text():
    """Parser texto plano devuelve resultados cuando no hay DLA2."""
    rows = parse_costs_over_time(PLAIN_PRIIPS)
    assert len(rows) >= 1, f"Expected >=1 row from plain text, got {len(rows)}"
    assert all(r['source'] == 'PLAIN_TEXT' for r in rows)

def test_costs_over_time_source_dla2():
    """Resultado DLA2 tiene source='DLA2'."""
    rows = parse_costs_over_time(DLA2_BZ4D7085)
    assert all(r['source'] == 'DLA2' for r in rows)


# ======================================================================
# Tests: parse_costs_composition
# ======================================================================

def test_composition_bz4d7085():
    """
    Test 3: IE00BZ4D7085 — entry=0%, entry_max≈5%, management≈0.10%, transaction≈0.02%.
    """
    result = parse_costs_composition(DLA2_BZ4D7085)
    assert result, f"Expected non-empty result, got {result}"

    # entry_fee_pct = 0%
    assert 'entry_fee_pct' in result, f"entry_fee_pct missing. keys={list(result)}"
    assert result['entry_fee_pct'] == 0.0, f"entry_fee_pct={result['entry_fee_pct']}"

    # entry_fee_max_pct: el texto final menciona "Hasta el 5%"
    if 'entry_fee_max_pct' in result:
        assert abs(result['entry_fee_max_pct'] - 0.05) < 0.001, (
            f"entry_fee_max_pct={result['entry_fee_max_pct']}"
        )

    # management_fee_pct ≈ 0.001 (0.10%)
    assert 'management_fee_pct' in result, f"management_fee_pct missing. keys={list(result)}"
    assert abs(result['management_fee_pct'] - 0.001) < 0.0005, (
        f"management_fee_pct={result['management_fee_pct']}"
    )

    # transaction_cost_pct ≈ 0.0002 (0.02%)
    assert 'transaction_cost_pct' in result, f"transaction_cost_pct missing."
    assert abs(result['transaction_cost_pct'] - 0.0002) < 0.0001, (
        f"transaction_cost_pct={result['transaction_cost_pct']}"
    )

def test_composition_lu1502282632_entry_max():
    """
    Test 4: LU1502282632 — entry_fee_max_pct = 3.50% = 0.035.
    """
    result = parse_costs_composition(DLA2_LU1502282632)
    assert 'entry_fee_max_pct' in result, f"entry_fee_max_pct missing. keys={list(result)}"
    assert abs(result['entry_fee_max_pct'] - 0.035) < 0.001, (
        f"entry_fee_max_pct={result['entry_fee_max_pct']}"
    )

def test_composition_fr0000989626_entry_pct_and_eur():
    """FR0000989626 — entry_fee_pct=0.005 (0.50%), entry_fee_eur=50.0."""
    result = parse_costs_composition(DLA2_FR0000989626)
    assert 'entry_fee_pct' in result
    assert abs(result['entry_fee_pct'] - 0.005) < 0.001, (
        f"entry_fee_pct={result['entry_fee_pct']}"
    )
    if 'entry_fee_eur' in result:
        assert result['entry_fee_eur'] == 50.0, f"entry_fee_eur={result['entry_fee_eur']}"

def test_composition_empty_text():
    """Texto vacío → dict vacío."""
    assert parse_costs_composition('') == {}
    assert parse_costs_composition(None) == {}  # type: ignore[arg-type]

def test_composition_no_table():
    """Texto sin tabla de composición → dict vacío."""
    result = parse_costs_composition("Texto genérico sin tabla.")
    assert result == {}

def test_composition_plain_text():
    """Parser texto plano extrae claves de composición."""
    result = parse_costs_composition(PLAIN_PRIIPS)
    assert result, f"Expected non-empty result from plain text, got {result}"
    # Debe encontrar al menos entry y management
    assert 'entry_fee_pct' in result or 'management_fee_pct' in result, (
        f"No cost types found. keys={list(result)}"
    )

def test_composition_exit_fee_zero():
    """Salida al 0% se parsea correctamente (no se omite por ser cero)."""
    result = parse_costs_composition(DLA2_BZ4D7085)
    assert 'exit_fee_pct' in result, f"exit_fee_pct missing. keys={list(result)}"
    assert result['exit_fee_pct'] == 0.0, f"exit_fee_pct={result['exit_fee_pct']}"


# ======================================================================
# Runner
# ======================================================================

def _run_all_tests() -> None:
    suite = [
        # _normalize_amount (tests 5-7 del traspaso + adicionales)
        test_normalize_european_thousands,
        test_normalize_european_decimal,
        test_normalize_integer,
        test_normalize_anglo_decimal,
        test_normalize_both_separators_eu,
        test_normalize_both_separators_en,
        test_normalize_none_on_empty,
        test_normalize_zero,
        # _parse_horizon_years (tests 8-10 del traspaso + adicionales)
        test_parse_horizon_1_year_es,
        test_parse_horizon_rhp,
        test_parse_horizon_3_months,
        test_parse_horizon_5_years,
        test_parse_horizon_6_months,
        test_parse_horizon_1_month,
        test_is_rhp_label_true,
        test_is_rhp_label_false,
        # parse_costs_over_time (tests 1-2 del traspaso + adicionales)
        test_costs_over_time_bz4d7085_rhp,
        test_costs_over_time_b45h7020_two_columns,
        test_costs_over_time_lu1502282632_two_horizons,
        test_costs_over_time_empty_text,
        test_costs_over_time_no_table,
        test_costs_over_time_plain_text,
        test_costs_over_time_source_dla2,
        # parse_costs_composition (tests 3-4 del traspaso + adicionales)
        test_composition_bz4d7085,
        test_composition_lu1502282632_entry_max,
        test_composition_fr0000989626_entry_pct_and_eur,
        test_composition_empty_text,
        test_composition_no_table,
        test_composition_plain_text,
        test_composition_exit_fee_zero,
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
