# proyecto1/tests/test_priips_cost_extractor.py
# -*- coding: utf-8 -*-
"""
Tests para priips_cost_extractor.py — BL-COST-4a (Sprint 2 S2-B).
Ground truth verificado ejecutando el extractor sobre los PDFs reales (2026-05-22).

Configurar KIDS_DIR apuntando al directorio con los KIDs en disco:
  - Variable de entorno KIDS_DIR, o
  - Valor por defecto: C:\\desarrollo\\fondos\\data\\kiids

Todos los tests que requieren disco se saltan automáticamente si el fichero
no está disponible. Los tests de funciones privadas y robustez son siempre
ejecutables sin acceso a disco.

ADVERTENCIA: los asserts reflejan el output REAL del parser en ruta PLAIN_TEXT.
Con DLA2 activo en producción los valores de Total_Costs_EUR de la 2ª columna
y algunos ACI cambiarán (ver tests _dla2_ideal al final).

Limitaciones conocidas del parser en ruta PLAIN_TEXT documentadas:
  - Bug de columnas duplicadas: 2ª fila hereda EUR/ACI de la 1ª.
  - ACI capturado vía EUR_ONLY (TCP = EUR/base), no desde celda %.
  - Algunas capturas espurias: Exit_Fee de IE00B45H7020, ACI_1Y de IE00BZ4D7085.
  - IE0032875985: ACI_1Y=5.67 es EUR_ONLY implied (567/10000*100), no ACI real.
"""

import os
import sys
import zipfile
import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR  = os.path.normpath(os.path.join(_TESTS_DIR, '..', 'core'))
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

# ---------------------------------------------------------------------------
# Directorio de KIDs en disco
# ---------------------------------------------------------------------------
KIDS_DIR = os.environ.get('KIDS_DIR', r'C:\desarrollo\fondos\data\kiids')
_KIDS_DIR_EXISTS = os.path.isdir(KIDS_DIR)


def load_kid_text(isin: str) -> str:
    """
    Carga el texto del KID desde disco.
    Soporta ZIP con N.txt (formato del Project) y PDF real (pdfplumber).
    """
    path = os.path.join(KIDS_DIR, f'{isin}.pdf')
    if not os.path.exists(path):
        raise FileNotFoundError(f"KID no disponible: {path}")
    if zipfile.is_zipfile(path):
        parts = []
        with zipfile.ZipFile(path) as z:
            txts = sorted(n for n in z.namelist() if n.endswith('.txt'))
            for n in txts:
                parts.append(z.read(n).decode('utf-8', errors='replace'))
        return '\n'.join(parts)
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return '\n'.join((p.extract_text() or '') for p in pdf.pages)


def _available(isin: str) -> bool:
    """True si el KID está en disco."""
    if not _KIDS_DIR_EXISTS:
        return False
    return os.path.exists(os.path.join(KIDS_DIR, f'{isin}.pdf'))


# ---------------------------------------------------------------------------
# Fixture autouse: activa kill-switch para todos los tests
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)


def _run(isin: str, **kwargs):
    from priips_cost_extractor import extract_priips_costs
    return extract_priips_costs(load_kid_text(isin), isin, **kwargs)


# ===========================================================================
# §5.1 — Kill-switch
# ===========================================================================

def test_killswitch_off_returns_empty(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', False)
    assert ext.extract_priips_costs("texto cualquiera", "TEST") == {}


def test_killswitch_on_processes(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)
    text = (
        "Documento de datos fundamentales\n"
        "Costes a lo largo del tiempo\n"
        "Composición de los costes\n"
        "Incidencia anual de los costes\n"
        "Período de mantenimiento recomendado: 3 años\n"
        "Escenarios de rentabilidad\n"
    )
    o = ext.extract_priips_costs(text, 'SYNTH')
    assert o.get('KID_Format') == 'PRIIPS_KID'
    assert 'Cost_Extraction_Quality' in o
    assert '_cost_schedule_rows' in o


# ===========================================================================
# §5.2 — Funciones privadas
# ===========================================================================

def test_ratio_to_pct():
    from priips_cost_extractor import _ratio_to_pct
    assert _ratio_to_pct(0.0525) == 5.25
    assert _ratio_to_pct(0.001)  == 0.1
    assert _ratio_to_pct(0.0)    == 0.0
    assert _ratio_to_pct(None) is None


def test_extract_rhp_years_anios():
    from priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("Período de mantenimiento recomendado: 3 años") == 3.0
    assert _extract_rhp_years("Recommended Holding Period: 5 years") == 5.0
    assert _extract_rhp_years("período de mantenimiento recomendado: 1 año") == 1.0


def test_extract_rhp_years_meses():
    from priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("Período de mantenimiento recomendado: 3 meses") == 0.25
    assert abs(_extract_rhp_years("período de mantenimiento recomendado: 6 meses") - 0.5) < 0.001


def test_extract_rhp_years_ausente():
    from priips_cost_extractor import _extract_rhp_years
    assert _extract_rhp_years("texto sin rhp") is None
    assert _extract_rhp_years("") is None


def test_norm_existing_oc():
    from priips_cost_extractor import _norm_existing_oc
    assert abs(_norm_existing_oc(2.4)   - 0.024) < 1e-9
    assert abs(_norm_existing_oc(0.70)  - 0.007) < 1e-9
    assert abs(_norm_existing_oc(0.007) - 0.007) < 1e-9
    assert _norm_existing_oc(None) is None


def test_detect_oc_aci_mismatch_positivo():
    from priips_cost_extractor import _detect_oc_aci_mismatch, _norm_existing_oc
    oc = 2.4
    assert _detect_oc_aci_mismatch(oc, _norm_existing_oc(oc), 0.007, 0.024) is True


def test_detect_oc_aci_mismatch_negativo_ter_cercano():
    from priips_cost_extractor import _detect_oc_aci_mismatch, _norm_existing_oc
    oc = 1.53
    assert _detect_oc_aci_mismatch(oc, _norm_existing_oc(oc), 0.0153, 0.024) is False


def test_detect_oc_aci_mismatch_none_guard():
    from priips_cost_extractor import _detect_oc_aci_mismatch
    assert _detect_oc_aci_mismatch(None, None, 0.007, 0.024) is False
    assert _detect_oc_aci_mismatch(2.4, 0.024, None, 0.024) is False
    assert _detect_oc_aci_mismatch(2.4, 0.024, 0.007, None) is False


# ===========================================================================
# §5.5 — Robustez (sin acceso a disco)
# ===========================================================================

def test_no_exception_on_garbage(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)
    o = ext.extract_priips_costs("\x00\x01 basura sin estructura |||", "BAD")
    assert isinstance(o, dict)
    assert 'Cost_Extraction_Quality' in o
    assert '_cost_schedule_rows' in o


def test_empty_text(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)
    o = ext.extract_priips_costs("", "EMPTY")
    assert o['KID_Format'] == 'UNKNOWN'
    assert o['Cost_Extraction_Quality'] == 'NONE'


def test_ucits_returns_none_quality(monkeypatch):
    import priips_cost_extractor as ext
    monkeypatch.setattr(ext, 'PRIIPS_COST_EXTRACTION_ENABLED', True)
    text = (
        "Datos fundamentales para el inversor\n"
        "Key investor information\n"
        "Gastos corrientes\n"
        "Entry charge exit charge ongoing charge\n"
    )
    o = ext.extract_priips_costs(text, "UCITS_TEST")
    assert o['KID_Format'] == 'UCITS_KIID'
    assert o['Cost_Extraction_Quality'] == 'NONE'
    assert o['_cost_schedule_rows'] == []


# ===========================================================================
# §5.4 — Lógica OC/ACI (P-3), con existing_oc simulado
# ===========================================================================

@pytest.mark.skipif(not _available('IE00BZ4D7085'), reason="KID IE00BZ4D7085 no disponible")
def test_oc_fill_when_null():
    """
    existing_oc=None + TER reconstruido → devuelve Ongoing_Charge_Recurrent.
    IE00BZ4D7085 (Polar): en esta versión del PDF el parser plano no capta
    Transaction_Cost_Pct, por lo que TER = solo mgmt = 1.11%.
    (El PDF del Project contenía transac 0.42%, probablemente versión distinta.)
    """
    o = _run('IE00BZ4D7085', existing_oc=None)
    assert 'Ongoing_Charge_Recurrent' in o
    # TER = mgmt (1.11%) sin transaction (no capturado en esta versión del PDF)
    assert abs(o['Ongoing_Charge_Recurrent'] - 1.11) < 0.05


@pytest.mark.skipif(not _available('IE0032875985'), reason="KID IE0032875985 no disponible")
def test_oc_mismatch_flag_when_existing_is_aci():
    """
    IE0032875985: BD trae OC=2.4 (=ACI@3Y), TER real ~0.49% → _oc_aci_mismatch=True,
    NO devuelve Ongoing_Charge_Recurrent (COALESCE-safe).
    Nota: para que el mismatch se detecte se necesita ACI_RHP. Con este KID en
    texto plano ACI_RHP es None (el parser no lo capta a 3Y), por lo que la
    heurística es conservadora y devuelve False. Este test verifica el comportamiento
    real; el escenario ideal requiere DLA2 activo.
    """
    o = _run('IE0032875985', existing_oc=2.4)
    # Con texto plano: ACI_RHP=None → mismatch conservador=False → no hay flag ni OC
    # (comportamiento correcto: no sobrescribe en caso de duda)
    assert 'Ongoing_Charge_Recurrent' not in o


@pytest.mark.skipif(not _available('IE00BZ4D7085'), reason="KID IE00BZ4D7085 no disponible")
def test_oc_no_action_when_existing_matches_ter():
    """
    existing_oc ya es un TER correcto → ni mismatch ni sobrescritura.
    """
    o = _run('IE00BZ4D7085', existing_oc=1.53)
    assert '_oc_aci_mismatch' not in o
    assert 'Ongoing_Charge_Recurrent' not in o


# ===========================================================================
# §5.3 — Tests por ISIN — Ground truth verificado 2026-05-22
# Los valores asertan el output REAL del extractor sobre texto plano.
# ===========================================================================

# ---------------------------------------------------------------------------
# FR0000989626 — Groupama, RHP=3 meses (0.25Y), HIGH
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('FR0000989626'), reason="KID FR0000989626 no disponible")
def test_fr0000989626():
    """
    Groupama FR0000989626: RHP=0.25Y (3 meses).
    ACI_1Y AUSENTE (no hay columna 1Y — P-5).
    Entry_Fee_Pct_Max AUSENTE (parser plano no capta "hasta X%" en este layout).
    ACI_RHP=0.54%, EUR=54. Una sola fila de schedule H=0.25 Is_RHP=1.
    Calidad: HIGH (EUR/base = 54/10000 = 0.54% ≈ ACI — coinciden a horizonte corto).
    Ground truth verificado 2026-05-22.
    """
    o = _run('FR0000989626')
    assert o['KID_Format']    == 'PRIIPS_KID'
    assert abs(o['Cost_RHP_Years'] - 0.25) < 0.01
    assert 'ACI_1Y' not in o
    assert 'Entry_Fee_Pct_Max' not in o
    assert abs(o['ACI_RHP'] - 0.54) < 0.02
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert abs(rows[0]['Horizon_Years'] - 0.25) < 0.01
    assert rows[0]['Is_RHP'] == 1
    assert abs(rows[0]['Total_Costs_EUR'] - 54.0) < 1.0
    assert abs(rows[0]['Annual_Impact_Pct'] - 0.54) < 0.02


# ---------------------------------------------------------------------------
# IE0032875985 — PIMCO, RHP=3Y, EUR, MEDIUM_EUR
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('IE0032875985'), reason="KID IE0032875985 no disponible")
def test_ie0032875985():
    """
    RHP=3Y, moneda EUR.
    ACI_1Y=5.67 (EUR_ONLY implied: 567/10000*100 — NO es ACI real del doc).
    Management_Fee_Pct=0.49%. Calidad MEDIUM_EUR.
    Bug columnas: schedule H=1 y H=3 tienen mismo EUR (567).
    """
    o = _run('IE0032875985')
    assert o['KID_Currency']    == 'EUR'
    assert o['Cost_RHP_Years']  == 3.0
    assert abs(o['ACI_1Y'] - 5.67) < 0.1         # EUR_ONLY implied
    assert abs(o['Management_Fee_Pct'] - 0.49) < 0.02
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert 1.0 in rows and 3.0 in rows
    assert abs(rows[1.0]['Total_Costs_EUR'] - 567.0) < 1.0
    assert abs(rows[3.0]['Total_Costs_EUR'] - 567.0) < 1.0  # bug columnas
    assert rows[3.0]['Is_RHP'] == 1


# ---------------------------------------------------------------------------
# IE00B45H7020 — BlackRock, RHP=1Y, USD, HIGH, fusión PK
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('IE00B45H7020'), reason="KID IE00B45H7020 no disponible")
def test_ie00b45h7020():
    """
    RHP=1Y, moneda USD. Una columna → fusión PK (RHP=1Y ≡ horizonte 1Y).
    ACI_RHP=0.1%, EUR=12. Management=0.10%, Transaction=0.02%.
    Exit_Fee_Max=0.10% (captura espuria del ACI — bug parser plano conocido).
    Calidad: HIGH.
    """
    o = _run('IE00B45H7020')
    assert o['KID_Currency']    == 'USD'
    assert o['Cost_RHP_Years']  == 1.0
    assert abs(o['ACI_RHP']              - 0.10) < 0.01
    assert abs(o['Management_Fee_Pct']   - 0.10) < 0.01
    assert abs(o['Transaction_Cost_Pct'] - 0.02) < 0.01
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert rows[0]['Horizon_Years'] == 1.0
    assert rows[0]['Is_RHP']        == 1     # fusión PK
    assert abs(rows[0]['Total_Costs_EUR'] - 12.0) < 1.0


# ---------------------------------------------------------------------------
# IE00BZ4D7085 — Polar Capital, RHP=5Y, EUR, MEDIUM_EUR
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('IE00BZ4D7085'), reason="KID IE00BZ4D7085 no disponible")
def test_ie00bz4d7085():
    """
    Polar Capital IE00BZ4D7085: RHP=5Y, moneda EUR.
    Entry_Fee_Max=5.0%, Exit_Fee_Max=0.0% (captura "0%" salida).
    Management_Fee_Pct=1.11%. Transaction_Cost_Pct AUSENTE en este PDF.
    ACI_1Y=1.53% (EUR_ONLY implied: 153/10000*100 — es el total del RHP, no el 1Y real).
    Bug columnas: H=1 y H=5 tienen mismo EUR=153 (herencia plano).
    Calidad: MEDIUM_EUR (ACI_RHP=None por RHP≠1Y; ancla=vr_1y=EUR_ONLY).
    Ground truth verificado 2026-05-22.
    """
    o = _run('IE00BZ4D7085')
    assert o['KID_Currency']   == 'EUR'
    assert o['Cost_RHP_Years'] == 5.0
    assert abs(o['Entry_Fee_Pct_Max']  - 5.0)  < 0.1
    assert abs(o['Management_Fee_Pct'] - 1.11) < 0.05
    assert 'Transaction_Cost_Pct' not in o
    assert abs(o['ACI_1Y'] - 1.53) < 0.05        # EUR_ONLY implied 153/10000*100
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert 1.0 in rows and 5.0 in rows
    assert rows[5.0]['Is_RHP'] == 1
    assert abs(rows[5.0]['Total_Costs_EUR'] - 153.0) < 1.0
    assert abs(rows[1.0]['Total_Costs_EUR'] - 153.0) < 1.0   # bug columnas: hereda EUR de RHP


# ---------------------------------------------------------------------------
# LU0135992385 — Schroders, RHP=1Y, MEDIUM_EUR, composición vacía
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('LU0135992385'), reason="KID LU0135992385 no disponible")
def test_lu0135992385():
    """
    RHP=1Y. parse_costs_composition={} (layout con [0.29%] entre corchetes).
    ACI_1Y=0.30%, Total=30 EUR.
    Management_Fee_Pct AUSENTE (composición vacía).
    Calidad: HIGH — el PDF en disco tiene ACI en celda %, vr_1y=OK.
    (El PDF del Project producía MEDIUM_EUR; versiones distintas del documento.)
    """
    o = _run('LU0135992385')
    assert o['Cost_RHP_Years'] == 1.0
    assert abs(o['ACI_1Y'] - 0.30) < 0.05
    assert 'Management_Fee_Pct' not in o
    assert o['Cost_Extraction_Quality'] == 'HIGH'
    rows = o['_cost_schedule_rows']
    assert len(rows) == 1
    assert rows[0]['Is_RHP'] == 1
    assert abs(rows[0]['Total_Costs_EUR'] - 30.0) < 1.0


# ---------------------------------------------------------------------------
# LU1084165304 — Fidelity, RHP=5Y, MEDIUM_EUR
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('LU1084165304'), reason="KID LU1084165304 no disponible")
def test_lu1084165304():
    """
    RHP=5Y. ACI no capturado en celda % (parser plano).
    ACI_1Y=7.13 (EUR_ONLY implied: 713/10000*100).
    Entry_Fee_Max=5.25%, Management=1.88%, Transaction=0.22%.
    Bug columnas: H=5 hereda EUR=713 de H=1.
    """
    o = _run('LU1084165304')
    assert o['Cost_RHP_Years'] == 5.0
    assert abs(o['ACI_1Y'] - 7.13) < 0.1          # EUR_ONLY implied
    assert abs(o['Entry_Fee_Pct_Max']  - 5.25) < 0.05
    assert abs(o['Management_Fee_Pct'] - 1.88) < 0.05
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert 1.0 in rows and 5.0 in rows
    assert abs(rows[1.0]['Total_Costs_EUR'] - 713.0) < 1.0
    assert rows[5.0]['Is_RHP'] == 1


# ---------------------------------------------------------------------------
# LU1502282632 — Candriam, RHP=6Y, MEDIUM_EUR
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _available('LU1502282632'), reason="KID LU1502282632 no disponible")
def test_lu1502282632():
    """
    RHP=6Y. Entry_Fee_Max=3.5%, Management=1.94%, Transaction=0.08%.
    ACI_1Y=5.76 (EUR_ONLY implied: 576/10000*100).
    Bug columnas: H=6 hereda EUR=576 de H=1.
    """
    o = _run('LU1502282632')
    assert o['Cost_RHP_Years'] == 6.0
    assert abs(o['Entry_Fee_Pct_Max']  - 3.50) < 0.05
    assert abs(o['Management_Fee_Pct'] - 1.94) < 0.05
    assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert 1.0 in rows and 6.0 in rows
    assert abs(rows[1.0]['Total_Costs_EUR'] - 576.0) < 1.0
    assert rows[6.0]['Is_RHP'] == 1


# ===========================================================================
# ISINs nuevos — Descubrimiento automático
# Se ejecutan solo si el KID está disponible en KIDS_DIR.
# Verifican propiedades mínimas invariantes (no valores exactos).
# Una vez ejecutados en tu máquina, reemplazar los asserts mínimos
# con los valores reales reportados por discover_kid_output().
# ===========================================================================

_NEW_ISINS = [
    'LU0070177588', 'LU0073230426', 'LU0128640439', 'LU0135992385',
    'LU0210536867', 'LU0213962813', 'LU0232465467', 'LU0236146428',
    'LU0256839274', 'LU0607519195', 'LU0726357873', 'LU1133289592',
    'LU1873127366', 'LU1959429272',
]


def _assert_invariants(o: dict, isin: str):
    """
    Propiedades invariantes que debe cumplir cualquier output del extractor.
    Úsalas mientras no tengas los valores exactos verificados.
    """
    assert 'KID_Format' in o,               f"{isin}: KID_Format ausente"
    assert 'Cost_Extraction_Quality' in o,  f"{isin}: Cost_Extraction_Quality ausente"
    assert '_cost_schedule_rows' in o,      f"{isin}: _cost_schedule_rows ausente"
    assert isinstance(o['_cost_schedule_rows'], list), f"{isin}: schedule no es lista"

    quality = o['Cost_Extraction_Quality']
    assert quality in ('HIGH','MEDIUM_CROSS','MEDIUM_EUR','MEDIUM_PCT','LOW','NONE'), \
        f"{isin}: calidad '{quality}' no reconocida"

    # Si hay schedule rows, verificar estructura de cada fila
    for r in o['_cost_schedule_rows']:
        assert 'Horizon_Years' in r,     f"{isin}: fila sin Horizon_Years"
        assert 'Is_RHP' in r,            f"{isin}: fila sin Is_RHP"
        assert 'Source' in r,            f"{isin}: fila sin Source"
        assert r['Source'] == 'PRIIPS_COSTS_OVER_TIME', \
            f"{isin}: Source inesperado '{r['Source']}'"
        hy = r['Horizon_Years']
        assert 0 < hy <= 50,             f"{isin}: Horizon_Years={hy} fuera de CHECK"

    # Campos numéricos deben ser >= 0 si presentes
    for field in ('ACI_1Y','ACI_RHP','Entry_Fee_Pct_Max','Exit_Fee_Pct_Max',
                  'Management_Fee_Pct','Transaction_Cost_Pct','Performance_Fee_Pct',
                  'Ongoing_Charge_Recurrent','Cost_RHP_Years'):
        if field in o and o[field] is not None:
            assert o[field] >= 0, f"{isin}: {field}={o[field]} negativo"


@pytest.mark.parametrize("isin", _NEW_ISINS)
def test_new_isin_invariants(isin):
    """
    Verifica propiedades mínimas invariantes para los ISINs nuevos.
    Se salta si el KID no está en KIDS_DIR.
    Para fijar asserts exactos: ejecutar discover_kid_output(isin) y
    copiar los valores en un test dedicado (ver plantilla al final).
    """
    if not _available(isin):
        pytest.skip(f"KID {isin} no disponible en {KIDS_DIR}")
    o = _run(isin)
    _assert_invariants(o, isin)


# ===========================================================================
# Helper de descubrimiento — ejecutar manualmente para obtener ground truth
# ===========================================================================

def discover_kid_output(isin: str) -> None:
    """
    Imprime el output completo del extractor para un ISIN.
    Usar desde consola para fijar asserts exactos:
        python -X utf8 -c "
          import sys; sys.path.insert(0,'core')
          from tests.test_priips_cost_extractor import discover_kid_output
          discover_kid_output('LU0070177588')
        "
    """
    import priips_cost_extractor as ext
    ext.PRIIPS_COST_EXTRACTION_ENABLED = True
    o = ext.extract_priips_costs(load_kid_text(isin), isin)
    rows = o.pop('_cost_schedule_rows', [])
    print(f'\n### {isin}')
    for k, v in sorted(o.items()):
        if not k.startswith('_'):
            val = f'{v:.4f}' if isinstance(v, float) else repr(v)
            print(f'  {k}: {val}')
    print('  _cost_schedule_rows:')
    for r in rows:
        hy  = r.get('Horizon_Years')
        rhp = r.get('Is_RHP')
        eur = r.get('Total_Costs_EUR')
        aip = r.get('Annual_Impact_Pct')
        tcp = r.get('Total_Costs_Pct')
        print(f'    H={hy}, Is_RHP={rhp}, EUR={eur}, AIP={aip}, TCP={tcp}')


# ---------------------------------------------------------------------------
# PLANTILLA para tests exactos de ISINs nuevos
# Copia, rellena con discover_kid_output() y desccomenta.
# ---------------------------------------------------------------------------

# @pytest.mark.skipif(not _available('LU0070177588'), reason="KID LU0070177588 no disponible")
# def test_lu0070177588():
#     """
#     <Fondo/gestora>: RHP=Xy, moneda XXX.
#     Ground truth verificado: <fecha>.
#     """
#     o = _run('LU0070177588')
#     assert o['KID_Format']   == 'PRIIPS_KID'
#     assert o['Cost_RHP_Years'] == X.0
#     # assert abs(o['ACI_1Y'] - X.XX) < 0.05
#     # assert abs(o['Management_Fee_Pct'] - X.XX) < 0.05
#     # assert o['Cost_Extraction_Quality'] == 'MEDIUM_EUR'  # ajustar
#     # rows = o['_cost_schedule_rows']
#     # assert rows[0]['Is_RHP'] == 1


# ===========================================================================
# §5.6 — Tests de regresión DLA2 (skip hasta BL-DLA-2 en producción)
# Valores ideales documentados para validación futura.
# ===========================================================================

@pytest.mark.skip(reason="requiere DLA2_Table_Text activo — BL-DLA-2 producción")
def test_ie00bjgt6q17_dla2_ideal():
    """Con DLA2: H=3Y → EUR=650, ACI_RHP=2.1% (no 3.6% heredado de 1Y)."""
    o = _run('IE00BJGT6Q17')
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert abs(rows[3.0]['Total_Costs_EUR'] - 650.0) < 1.0
    assert abs(o['ACI_RHP'] - 2.1) < 0.1


@pytest.mark.skip(reason="requiere DLA2_Table_Text activo — BL-DLA-2 producción")
def test_lu1084165304_dla2_ideal():
    """Con DLA2: H=5Y → EUR=1904."""
    o = _run('LU1084165304')
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert abs(rows[5.0]['Total_Costs_EUR'] - 1904.0) < 5.0


@pytest.mark.skip(reason="requiere DLA2_Table_Text activo — BL-DLA-2 producción")
def test_ie00bz4d7085_dla2_ideal():
    """Con DLA2: H=5Y → EUR=1360 (no 153 del text plano)."""
    o = _run('IE00BZ4D7085')
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert abs(rows[5.0]['Total_Costs_EUR'] - 1360.0) < 5.0


@pytest.mark.skip(reason="requiere DLA2_Table_Text activo — BL-DLA-2 producción")
def test_lu1502282632_dla2_ideal():
    """Con DLA2: H=6Y → EUR=3878."""
    o = _run('LU1502282632')
    rows = {r['Horizon_Years']: r for r in o['_cost_schedule_rows']}
    assert abs(rows[6.0]['Total_Costs_EUR'] - 3878.0) < 5.0


# ---------------------------------------------------------------------------
# FIX-ACI-RETURN-GUARD + FIX-ACI-RHP-COLLAPSED (ACT-06, 2026-08-19)
# Recover ACI_RHP on layouts where the values-path parser leaves the genuine
# cost impact visible-but-unbound. All extractor-side, parser untouched.
# ---------------------------------------------------------------------------
from priips_cost_extractor import extract_priips_costs, _pick_aci_for_horizon

_DB_PATH = r'C:\desarrollo\fondos\db\fondos.sqlite'
_DB_EXISTS = os.path.exists(_DB_PATH)


def _fed_from_db(isin: str):
    """Load Raw_KIID_Text + DLA2_Table_Text for a fund (KIID_Class=1)."""
    import sqlite3
    con = sqlite3.connect(_DB_PATH)
    try:
        row = con.execute(
            "SELECT Raw_KIID_Text, DLA2_Table_Text FROM fund_kiid_metadata "
            "WHERE ISIN=? AND KIID_Class=1", (isin,)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return (row[0] or '') + '\n' + (row[1] or '')


def test_pick_aci_skips_none_is_rhp():
    """A bogus is_rhp row with aci_pct=None must not shadow the numeric-horizon
    match: _pick_aci_for_horizon(want_rhp=True) falls through to the 5Y column."""
    rows = [
        {'horizon_years': 1.0, 'aci_pct': 0.004, 'is_rhp': False},
        {'horizon_years': 5.0, 'aci_pct': 0.004, 'is_rhp': False},
        {'horizon_years': -1.0, 'aci_pct': None, 'is_rhp': True},
    ]
    assert _pick_aci_for_horizon(rows, 5.0, True) == 0.004
    # A valid is_rhp row is still honoured first.
    rows2 = [{'horizon_years': 5.0, 'aci_pct': 0.02, 'is_rhp': True}]
    assert _pick_aci_for_horizon(rows2, 5.0, True) == 0.02


def test_bogus_isrhp_return_footnote_recovers_via_longest():
    """RC-1: the '(*) ...average return per year is projected to be 4.6% before
    costs' footnote yields a bogus is_rhp=4.6% entry that the >5 ratio guard
    rejects; FIX-ACI-RHP-LONGEST then recovers the genuine 0.4% RHP column.
    Self-contained (synthetic KID text), no disk/DB."""
    text = (
        "Periodo de mantenimiento recomendado: 5 anos\n"
        "Costes a lo largo del tiempo\n"
        "En caso de salida despues de 1 ano   En caso de salida despues de 5 anos\n"
        "Costes totales 38 EUR 233 EUR Annual cost Impact (*)\n"
        "0.4% 0.4% (*) This illustrates how costs reduce your return. If you exit "
        "at the recommended holding period your average return per year is "
        "projected to be 4.6% before costs and 4.2% after costs."
    )
    out = extract_priips_costs(text, 'TEST_RETURN_GUARD')
    assert out.get('ACI_RHP') == 0.4, out.get('ACI_RHP')


@pytest.mark.skipif(not _DB_EXISTS, reason="fondos.sqlite no disponible")
def test_ishares_en_annual_cost_impact_recovers():
    """RC-1 on the real English iShares layout (IE00B3D07F16): bogus is_rhp
    return figure rejected, real 0.4% RHP recovered via LONGEST."""
    fed = _fed_from_db('IE00B3D07F16')
    if fed is None or 'annual cost' not in fed.lower():
        pytest.skip("KIID text for IE00B3D07F16 not present/changed")
    assert extract_priips_costs(fed, 'IE00B3D07F16').get('ACI_RHP') == 0.4


@pytest.mark.skipif(not _DB_EXISTS, reason="fondos.sqlite no disponible")
def test_neuberger_collapsed_single_value_recovers():
    """RC-2 on the real Neuberger Berman full-grid layout (IE00BLLXGV72): the
    incidencia row collapsed to a single 1.2% value (RHP column None);
    FIX-ACI-RHP-COLLAPSED anchors ACI_RHP on it."""
    fed = _fed_from_db('IE00BLLXGV72')
    if fed is None or 'incidencia' not in fed.lower():
        pytest.skip("KIID text for IE00BLLXGV72 not present/changed")
    assert extract_priips_costs(fed, 'IE00BLLXGV72').get('ACI_RHP') == 1.2


# ---------------------------------------------------------------------------
# FIX-COST-DECIMAL-YEAR (FR0000447823 root cause)
# ---------------------------------------------------------------------------

def test_fix_cost_decimal_year_parse():
    """FIX-COST-DECIMAL-YEAR: column label '0,00396825 años' must be parsed as
    ~0.00396825 years, NOT 396825.0.  Old regex '(\\d+)' matched '00396825'
    (digit-run after the decimal comma); new pattern '(\\d+(?:[.,]\\d+)?)' captures
    the full decimal number.

    Self-contained synthetic text — no disk/DB needed.
    The KID has a single ultra-short column ("0,00396825 años") with 1% ACI.
    After the fix:
      • horizon_years ≈ 0.00396825 (passes 0 < hy <= 50 → stays in schedule)
      • FIX-ACI-RHP-SINGLE fires (no horizon > 1Y) → ACI_RHP = 1%
      • FIX-ACI-SCHEDULE-INJECT fires → synthesized Is_RHP=1 row present
    """
    from priips_cost_extractor import extract_priips_costs
    from cost_table_parser import _parse_horizon_years

    # Unit test on the parser function
    hy = _parse_horizon_years("Si sale después de 0,00396825 años")
    assert abs(hy - 0.00396825) < 1e-7, f"Expected ~0.00396825, got {hy}"

    # Integration test: synthetic PRIIPS KID text with fractional-year column.
    # Three PRIIPS signals (>= 3 needed for PRIIPS_KID format detection):
    # "período de mantenimiento recomendado" + "costes a lo largo del tiempo"
    # + "composición de los costes".
    text = (
        "Período de mantenimiento recomendado: 1 año\n"
        "Costes a lo largo del tiempo\n"
        "Si sale después de 0,00396825 años\n"
        "Costes totales 100 EUR\n"
        "Incidencia anual de los costes 1%\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "FR0000447823_SYNTH")
    # ACI_RHP must be set (via SINGLE fallback)
    assert out.get("ACI_RHP") is not None, "ACI_RHP should be recovered by SINGLE fallback"
    # Schedule must have an Is_RHP=1 row (via INJECT or primary path)
    rhp_rows = [r for r in out.get("_cost_schedule_rows", []) if r.get("Is_RHP") == 1]
    assert rhp_rows, "Expected at least one Is_RHP=1 schedule row (FIX-ACI-SCHEDULE-INJECT)"
    assert rhp_rows[0].get("Annual_Impact_Pct") is not None


# ---------------------------------------------------------------------------
# FIX-ACI-SCHEDULE-INJECT
# ---------------------------------------------------------------------------

def test_fix_aci_schedule_inject_fires_when_fallback_sets_aci_rhp():
    """FIX-ACI-SCHEDULE-INJECT: when FIX-ACI-RHP-SINGLE/LONGEST/COLLAPSED
    recovers ACI_RHP from the OT table but _build_schedule_rows discards the
    is_rhp row (rhp_years=None), the post-fallback injection must synthesize
    a minimal Is_RHP=1 row so P2/P3 can access Annual_Impact_Pct.

    Scenario: single-column OT table with no explicit RHP label → SINGLE fires.
    Self-contained synthetic text — no disk/DB needed.
    """
    from priips_cost_extractor import extract_priips_costs

    # No "período de mantenimiento recomendado" line → rhp_years=None.
    # Single OT column → FIX-ACI-RHP-SINGLE fires → ACI_RHP=2%.
    # _build_schedule_rows: is_rhp row discarded (rhp_years=None).
    # Injection: synthesized Is_RHP=1 row with Annual_Impact_Pct=2.0.
    # Three PRIIPS signals: "costes a lo largo del tiempo" + "composición de
    # los costes" + "incidencia anual de los costes".
    text = (
        "Costes a lo largo del tiempo\n"
        "En caso de salida después de 1 año\n"
        "Costes totales 200 EUR\n"
        "Incidencia anual de los costes 2%\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "INJECT_SYNTH_TEST")
    assert out.get("ACI_RHP") == 2.0, f"ACI_RHP={out.get('ACI_RHP')}, expected 2.0"
    rhp_rows = [r for r in out.get("_cost_schedule_rows", []) if r.get("Is_RHP") == 1]
    assert rhp_rows, "FIX-ACI-SCHEDULE-INJECT must add an Is_RHP=1 row"
    assert rhp_rows[0].get("Annual_Impact_Pct") == 2.0, (
        f"Annual_Impact_Pct={rhp_rows[0].get('Annual_Impact_Pct')}, expected 2.0"
    )


# ---------------------------------------------------------------------------
# FIX-ACI-RHP-LONGEST >= 1Y (Wellington layout, 2026-08-23)
# ---------------------------------------------------------------------------

def test_fix_aci_rhp_longest_1y_wellington_layout():
    """Wellington layout: bogus is_rhp row (return projection 13.2%) rejected by
    >5 ratio guard; 5Y row has aci_pct=None (merged text "1.7% 1.7% cada año");
    1Y row has aci_pct=0.017. With >= 1.0 fix, LONGEST picks the 1Y row as
    best available approximation and sets ACI_RHP=1.7.

    Before the fix (> 1.0): 1Y was excluded → LONGEST found no candidates →
    ACI_RHP=None despite ACI_1Y=1.7 being correctly set.
    Self-contained (synthetic KID text), no disk/DB."""
    from priips_cost_extractor import extract_priips_costs

    # Synthetic replication of Wellington ES-language KID layout:
    # - "período de mantenimiento recomendado" label → bogus is_rhp row aci_pct=0.132
    # - 5 años column → total_cost_eur=0, aci_pct=None (layout merges the %)
    # - 1 año column → aci_pct=0.017
    text = (
        "Costes a lo largo del tiempo\n"
        "después de 1 año 5 años período de mantenimiento recomendado\n"
        "Costes totales 170 EUR 0 EUR\n"
        "Incidencia anual de los costes 1.7% 1.7% cada año\n"
        "(*) El rendimiento medio que se prevé del fondo es del 13.2% antes de deducir los costes\n"
        "período de mantenimiento recomendado\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "WELLINGTON_SYNTH_TEST")
    assert out.get("ACI_1Y") == 1.7, f"ACI_1Y={out.get('ACI_1Y')}, expected 1.7"
    assert out.get("ACI_RHP") == 1.7, (
        f"ACI_RHP={out.get('ACI_RHP')}: LONGEST should fall back to 1Y row "
        "when 5Y row has no aci_pct and bogus is_rhp row is rejected"
    )
    # FIX-SCHEDULE-IS-RHP-REPAIR: the Is_RHP=1 schedule row must carry the
    # corrected 1.7%, NOT the bogus 13.2% from the footnote return projection.
    rhp_rows = [r for r in out.get("_cost_schedule_rows", []) if r.get("Is_RHP") == 1]
    assert rhp_rows, "Is_RHP=1 schedule row must exist"
    assert rhp_rows[0].get("Annual_Impact_Pct") == 1.7, (
        f"Is_RHP=1 Annual_Impact_Pct={rhp_rows[0].get('Annual_Impact_Pct')}: "
        "schedule repair must overwrite the bogus 13.2 with the recovered 1.7"
    )


# ---------------------------------------------------------------------------
# FIX-ACI-LABEL-ANCHOR (scenario-table bleed, 2026-08-23)
#
# Root cause: parse_costs_over_time binds percentages by proximity to the cost
# heading. When the PERFORMANCE-SCENARIO table is interleaved in the pdfplumber
# text stream, the bound value is the stress-scenario average annual return
# (always negative) instead of the cost row. P0-ACI-RHP-GUARD then correctly
# rejects it -> ACI_RHP NULL although the KID does publish the figure.
# ---------------------------------------------------------------------------

def test_label_anchor_adjacent_scenario_bleed():
    """Pattern A (Dunas ES0175404013): the scenario table sits between the cost
    heading and the real cost row. Parser binds -77,24% (stress scenario); the
    true ACI 1,0% follows the "Impacto del coste anual" label directly.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "- Se invierten 10.000 EUR.\n"
        "En caso de salida después de 1 año\n"
        "En caso de salida después de 5 años\n"
        "€2.280 -77,24%\n"
        "€7.900 -21,01% €11.240\n"
        "12,43% €14.950\n"
        "Costes totales\n"
        "99 € 925 €\n"
        "Impacto del coste anual (*)\n"
        "1,0%\n"
        "(*)Refleja la medida en que los costes reducen su rendimiento cada año "
        "a lo largo del período de mantenimiento. Por ejemplo, el rendimiento "
        "medio que se prevé que obtendrá cada año será del 13,10% antes de "
        "deducir los costes y del 12,11% después de deducir los costes.\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
        # El documento real repite la etiqueta en la tabla de composición
        # (ES0175404013 @16112). Se incluye para fidelidad y porque
        # detect_kid_format exige >= 3 señales PRIIPs.
        "Incidencia anual de los costes en caso de salida después de 1 año\n"
        "0,96% del valor de su inversión por año.\n"
    )
    out = extract_priips_costs(text, "LABEL_ANCHOR_ADJACENT")

    assert out.get("ACI_RHP") == 1.0, (
        f"ACI_RHP={out.get('ACI_RHP')}: label anchor must recover 1,0%, "
        "not the -77,24% stress scenario"
    )
    # The footnote's 13,10% / 12,11% return projections must never be picked up.
    assert out.get("ACI_RHP") != 13.1
    assert out.get("ACI_1Y") != 12.11


def test_label_anchor_split_page_table():
    """Pattern B (GVC Gaesco ES0179692001): the cost table is split across pages.
    Header + EUR row close page N; the % row opens page N+1 with page furniture
    (page number, logo, document title, whole risk section) interleaved.
    The "cada año" suffix identifies the cost row positively despite the gap.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Inversión 10.000 EUR\n"
        "Costes totales\n"
        "DOCUMENTO DATOS FUNDAMENTALES\n"
        "Tramontana Retorno Absoluto Audaz, FI\n"
        "El indicador resumido de riesgo es una guía del nivel de riesgo de este "
        "producto en comparación con otros productos. Hemos clasificado este "
        "producto en la clase de riesgo 5 en una escala de 7. Este producto no "
        "incluye protección alguna contra la evolución futura del mercado.\n"
        "1 año 6 años\n"
        "531 € -94,7%\n"
        "1.248 €\n"
        "-29,3%\n"
        "-49,3% 5.074 €\n"
        "En caso de salida después de 6 años\n"
        "En caso de salida después de 1 año\n"
        "265 € 1.495 €\n"
        "2/3\n"
        "Impacto del coste anual (*)\n"
        "2,7% 2,5% cada año\n"
        "(*)Refleja la medida en que los costes reducen su rendimiento cada año.\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
        "Incidencia anual de los costes en caso de salida después de 1 año\n"
        "1,41% del valor de su inversión por año.\n"
    )
    out = extract_priips_costs(text, "LABEL_ANCHOR_SPLITPAGE")

    assert out.get("ACI_RHP") == 2.5, (
        f"ACI_RHP={out.get('ACI_RHP')}: must recover 2,5% (RHP column) across "
        "the page split, not the -94,7% stress scenario"
    )
    assert out.get("ACI_1Y") == 2.7, (
        f"ACI_1Y={out.get('ACI_1Y')}: must recover 2,7% (1Y column)"
    )
    rhp_rows = [r for r in out.get("_cost_schedule_rows", []) if r.get("Is_RHP") == 1]
    if rhp_rows:
        assert rhp_rows[0].get("Annual_Impact_Pct") == 2.5, (
            "Is_RHP=1 schedule row must carry the recovered 2,5%, not the "
            "bogus scenario value"
        )


def test_label_anchor_corrects_return_projection_under_guard():
    """The footnote return-projection can slip UNDER the guard cap and publish as
    a plausible-looking cost (LU2092974778: 23.28% stored, real ACI 1.42%).
    The anchor corrects it because the stored value matches the projection
    sentence verbatim and the delta is material.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costs over time\n"
        "Total costs\n"
        "if you exit after 1 year 0 EUR\n"
        "if you exit after 5 years 5 EUR\n"
        "recommended holding period 23.28%\n"
        "Annual Cost Impact* 1.17% 1.42%\n"
        "(*)This illustrates how costs reduce your return each year over the "
        "holding period. For example, it shows that if you exit at the "
        "recommended holding period your average return per year is projected "
        "to be 23.28% before costs and 21.86% after costs.\n"
        "Composition of costs\n"
        "Entry costs 0%\n"
    )
    out = extract_priips_costs(text, "LABEL_ANCHOR_CORRECT")

    assert out.get("ACI_RHP") != 23.28, (
        "the 23.28% return projection must never be published as a cost"
    )
    assert out.get("ACI_RHP") == 1.42, (
        f"ACI_RHP={out.get('ACI_RHP')}: anchor must correct to the real 1.42%"
    )


def test_label_anchor_does_not_correct_coincidental_match():
    """Guard against over-correction: when a correct ACI happens to equal a
    number in the projection sentence, the delta is tiny and the value must be
    left alone (LU0546920561: ACI 2.30 vs projection 2.3 -- coincidence).
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "En caso de salida después de 1 año\n"
        "Costes totales 230 EUR\n"
        "Incidencia anual de los costes 2,3%\n"
        "(*)Refleja la medida en que los costes reducen su rendimiento. El "
        "rendimiento medio que se prevé que obtendrá cada año será del 2,3% "
        "antes de deducir los costes.\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "LABEL_ANCHOR_NO_OVERCORRECT")

    assert out.get("ACI_RHP") == 2.3, (
        f"ACI_RHP={out.get('ACI_RHP')}: a coincidental match with the "
        "projection sentence must NOT trigger a correction"
    )


def test_label_anchor_is_fill_only_for_resolved_funds():
    """A fund whose ACI resolves normally must not be perturbed by the anchor."""
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "En caso de salida después de 1 año\n"
        "En caso de salida después de 5 años\n"
        "Costes totales 150 EUR 800 EUR\n"
        "Incidencia anual de los costes 1,5% 1,6%\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "LABEL_ANCHOR_FILLONLY")
    # The parser's own pick (1,5%) stands. The anchor would have offered 1,6%
    # for the RHP column, so this asserts the fill-only contract: a resolved
    # ACI_RHP is never overwritten by the anchor.
    assert out.get("ACI_RHP") == 1.5, (
        f"ACI_RHP={out.get('ACI_RHP')}: anchor must not override a value the "
        "parser already resolved"
    )


# ---------------------------------------------------------------------------
# FIX-ACI-ROW-TAIL-FEELINE + FIX-ACI-ANCHOR-PASSORDER (2026-08-23)
#
# The row-tail pass used to run across ALL labels before adjacency was even
# considered, and its terminator set accepted "al año" / "per year" -- the
# wording of FEE lines. A distant "10,00% al año" fee line therefore beat the
# real ACI sitting next to its own label (7 Polar Capital funds returned 10.00).
# ---------------------------------------------------------------------------

def test_anchor_prefers_adjacent_value_over_distant_fee_line():
    """Polar Capital layout: real ACI '0,9% 1,0%' is adjacent to the label; a
    fee line '10,00% al año' sits further down the same window. Adjacency must
    win, and 'al año' must not be treated as an ACI-row terminator at all.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Costes totales 93 EUR 718 EUR\n"
        "Incidencia anual de los costes (*) \n"
        "0,9% 1,0%\n"
        "(*) Refleja la medida en que los costes reducen su rendimiento cada año.\n"
        "Composición de los costes\n"
        "Costes de entrada 10,00% al año\n"
        "Costes de salida 0,00%\n"
    )
    out = extract_priips_costs(text, "ANCHOR_FEELINE_TRAP")

    assert out.get("ACI_RHP") == 1.0, (
        f"ACI_RHP={out.get('ACI_RHP')}: the adjacent 1,0% must win over the "
        "distant '10,00% al año' fee line"
    )
    assert out.get("ACI_RHP") != 10.0, "fee line must never be read as ACI"


def test_evidence_corrects_sub_3pp_bleed():
    """A real bleed with a delta of only 2.9pp: label says '4.6% 2.0% cada año'
    (RHP=2.0) but 4.90 -- the return projection -- was stored. The retired 3pp
    magnitude gate skipped this; the evidence test catches it because the label
    does not vouch for 4.90. Modelled on LU0119197159.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Costes totales 460 EUR 200 EUR\n"
        "recomendado 4.9%\n"
        "Incidencia anual de los costes (*) \n"
        "4.6% 2.0% cada año\n"
        "(*) Refleja la medida en que los costes reducen su rendimiento cada año "
        "a lo largo del período de mantenimiento. Por ejemplo, el rendimiento "
        "medio que se prevé que obtendrá cada año será del 4.9% antes de deducir "
        "los costes y del 2.9% después de deducir los costes.\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "EVIDENCE_SUB3PP_BLEED")

    assert out.get("ACI_RHP") != 4.9, (
        "4.9% is the return projection, not a cost -- must not be published"
    )
    assert out.get("ACI_RHP") == 2.0, (
        f"ACI_RHP={out.get('ACI_RHP')}: evidence test must recover the RHP "
        "column value 2.0 despite the delta being under the retired 3pp gate"
    )


def test_evidence_protects_label_vouched_coincidence():
    """The stored ACI (2,3%) legitimately equals a number in the projection
    sentence. Because the label vouches for 2,3%, it is a real cost and must
    survive untouched -- this is what an exact-match-only rule would corrupt.
    Modelled on LU0546920561.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "En caso de salida después de 1 año\n"
        "Costes totales 230 EUR\n"
        "Incidencia anual de los costes 2,3%\n"
        "(*) Refleja la medida en que los costes reducen su rendimiento. El "
        "rendimiento medio que se prevé que obtendrá cada año será del 2,3% "
        "antes de deducir los costes.\n"
        "Composición de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "EVIDENCE_VOUCHED_COINCIDENCE")

    assert out.get("ACI_RHP") == 2.3, (
        f"ACI_RHP={out.get('ACI_RHP')}: a label-vouched value must never be "
        "corrected, even though it matches the projection sentence"
    )


# ---------------------------------------------------------------------------
# FIX-ACI-RHP-COLUMN-DUP (2026-08-23)
#
# ACI_RHP ended up holding the 1-YEAR value. Two sub-mechanisms, one signature:
#   (a) PLAIN_TEXT emits two rows sharing the same aci_pct (duplication);
#   (b) DLA2 emits a single row tagged with the RHP horizon but carrying the
#       1-year figures.
# Both are corrected from the label's second column, guarded by the
# amortisation direction (1Y >= RHP).
# ---------------------------------------------------------------------------

def test_column_dup_recovers_rhp_from_second_label_value():
    """Duplication: both over-time rows carry 6.5%; the label reads
    '6.5% 3.1% cada ano' with RHP=3y, so ACI_RHP must become 3.1.
    Modelled on LU0152980495.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Periodo de mantenimiento recomendado: 3 anos\n"
        "En caso de salida despues de 1 ano\n"
        "En caso de salida despues de 3 anos\n"
        "Costes totales 647 EUR 647 EUR\n"
        "Incidencia anual de los costes (*) \n"
        "6.5% 3.1% cada ano\n"
        "Composicion de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "COLDUP_PLAIN")

    assert out.get("ACI_RHP") == 3.1, (
        f"ACI_RHP={out.get('ACI_RHP')}: must take the RHP column (3.1), not the "
        "duplicated 1-year value (6.5)"
    )
    assert out.get("ACI_1Y") == 6.5, "ACI_1Y must remain the 1-year value"


def test_column_dup_not_applied_when_direction_is_reversed():
    """Guard: when the label's first value is SMALLER than the second, the pair
    is not a 1Y/RHP cost pair -- costs amortise, they do not grow. Such funds
    must be left untouched. Modelled on LU2455947981 ('3,9% 11,8%').
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Periodo de mantenimiento recomendado: 3 anos\n"
        "En caso de salida despues de 1 ano\n"
        "En caso de salida despues de 3 anos\n"
        "Costes totales 390 EUR 390 EUR\n"
        "Incidencia anual de los costes *\n"
        "3,9% 11,8%\n"
        "Composicion de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "COLDUP_REVERSED")

    assert out.get("ACI_RHP") != 11.8, (
        "an 11.8% annual cost impact is not plausible; the reversed-direction "
        "guard must prevent this substitution"
    )


def test_column_dup_not_applied_when_rhp_is_one_year():
    """Guard: with RHP = 1 year, ACI_RHP == ACI_1Y is correct by definition."""
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Periodo de mantenimiento recomendado: 1 ano\n"
        "En caso de salida despues de 1 ano\n"
        "Costes totales 200 EUR\n"
        "Incidencia anual de los costes 2,0%\n"
        "Composicion de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "COLDUP_RHP1Y")
    assert out.get("ACI_RHP") == 2.0, (
        f"ACI_RHP={out.get('ACI_RHP')}: with RHP=1y the 1-year value is correct"
    )


# ---------------------------------------------------------------------------
# FIX-PROJECTION-BEFORE-COSTS (2026-08-23)
#
# The projection footnote's LEAD-IN wording varies by issuer, so anchoring on it
# missed ~100 funds whose projection (14.3-14.8%, just under the 15% cap) was
# published as ACI_RHP. The TRAILING "antes de costes" / "before costs" phrase
# is invariant and is what the detector now anchors on.
# ---------------------------------------------------------------------------

def test_projection_detected_via_trailing_before_costs_phrase():
    """BlackRock ES wording: 'se preve que su rentabilidad media anual sea del
    14.8% antes de costes' -- the lead-in differs from the older pattern, but
    the trailing phrase identifies it. Modelled on LU0106831901.
    """
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costes a lo largo del tiempo\n"
        "Periodo de mantenimiento recomendado: 5 anos\n"
        "Si sale despues de 1 ano\n"
        "Si sale despues de 5 anos\n"
        "Costes totales 760 EUR\n"
        "periodo de mantenimiento recomendado 14.8%\n"
        "Incidencia anual de los costes (*)\n"
        "7.6% 4.1% cada ano\n"
        "(*) Esto ilustra como los costes reducen su rendimiento. Por ejemplo, "
        "muestra que si sale durante el periodo de mantenimiento recomendado, se "
        "preve que su rentabilidad media anual sea del 14.8% antes de costes y "
        "del 10.8% despues de aplicar los costes.\n"
        "Composicion de los costes\n"
        "Costes de entrada 0%\n"
    )
    out = extract_priips_costs(text, "PROJ_BEFORE_COSTS")

    assert out.get("ACI_RHP") != 14.8, (
        "14.8% is the projected return before costs, not a cost"
    )
    assert out.get("ACI_RHP") == 4.1, (
        f"ACI_RHP={out.get('ACI_RHP')}: must resolve to the RHP column (4.1)"
    )


def test_projection_english_before_costs_phrase():
    """English equivalent: '... is 9.5% before costs and 6.1% after costs'."""
    from priips_cost_extractor import extract_priips_costs

    text = (
        "Costs over time\n"
        "Recommended holding period: 5 years\n"
        "If you exit after 1 year\n"
        "If you exit after 5 years\n"
        "Total costs 720 EUR\n"
        "recommended holding period 9.5%\n"
        "Annual cost impact (*)\n"
        "7.2% 3.5% each year\n"
        "(*) This illustrates how costs reduce your return. Your average annual "
        "return is 9.5% before costs and 6.1% after costs.\n"
        "Composition of costs\n"
        "Entry costs 0%\n"
    )
    out = extract_priips_costs(text, "PROJ_BEFORE_COSTS_EN")

    assert out.get("ACI_RHP") != 9.5, "9.5% is a return, not a cost"
    assert out.get("ACI_RHP") == 3.5, (
        f"ACI_RHP={out.get('ACI_RHP')}: must resolve to the RHP column (3.5)"
    )
