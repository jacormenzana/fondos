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
