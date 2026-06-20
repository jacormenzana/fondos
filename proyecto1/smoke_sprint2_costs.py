# smoke_sprint2_costs.py
# -*- coding: utf-8 -*-
"""
Smoke test del Sprint 2 de extracción de costes (BL-COST-4a/4b/4c).

Propósito:
    Verificar que ambos extractores (PRIIPs y UCITS) funcionan correctamente
    sobre los PDFs de muestra antes de ejecutar el pipeline completo.

Ejecutar desde la raíz del proyecto:
    cd C:\\desarrollo\\fondos
    python -X utf8 proyecto1\\smoke_sprint2_costs.py

Prerequisitos:
    - S2-B deployado: priips_cost_extractor.py OK
    - S2-C deployado: ucits_cost_extractor.py OK
    - conda env "des" activo
"""

import sys
import logging
from pathlib import Path

# ── Rutas canónicas (árbol verificado en tree_proyectos.txt) ──────────────
# smoke_sprint2_costs.py vive en: C:\desarrollo\fondos\proyecto1\
# Los módulos core viven en:      C:\desarrollo\fondos\proyecto1\core\
# shared vive en:                 C:\desarrollo\fondos\shared\
_SCRIPT_DIR = Path(__file__).resolve().parent          # .../proyecto1
_ROOT       = _SCRIPT_DIR.parent                       # .../fondos
_CORE_DIR   = _SCRIPT_DIR / "core"
_SHARED_DIR = _ROOT / "shared"

for _p in [str(_CORE_DIR), str(_SCRIPT_DIR), str(_ROOT), str(_SHARED_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Forzar kill-switch ON para el smoke (sin modificar config.py) ─────────
import priips_cost_extractor as _priips_ext
import ucits_cost_extractor  as _ucits_ext
_priips_ext.PRIIPS_COST_EXTRACTION_ENABLED = True
_ucits_ext.PRIIPS_COST_EXTRACTION_ENABLED  = True

from priips_cost_extractor import extract_priips_costs
from ucits_cost_extractor  import extract_ucits_costs
from cost_format_router    import detect_kid_format

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# ── PDFs de muestra (C:\desarrollo\fondos\data\kiids\) ───────────────────
#PDF_DIR = _ROOT / "data" / "kiids"
PDF_DIR = Path(r"C:\data\fondos\kiid")

_SAMPLE_ISINS = [
    "FR0000989626",  
    "LU0070177588",  
    "LU0213962813",  
    "LU0726357873",
    "IE0032875985",  
    "LU0073230426",  
    "LU0232465467",  
    "LU1133289592",
    "IE00B45H7020",  
    "LU0128640439",  
    "LU0236146428",  
    "LU1502282632",
    "IE00BZ4D7085",  
    "LU0135992385",  
    "LU0256839274",  
    "LU1873127366",
    "LU0006277684",  
    "LU0210536867",  
    "LU0607519195",  
    "LU1959429272",
    "IE00BZ4D7085",
    "LU1502282632",
    "LU1084165304",
    "IE00B45H7020",
    "FR0000989626",
    "IE0032875985",
    "LU0135992385",
    "IE00BJGT6Q17",    
]

# ── Textos UCITS sintéticos ───────────────────────────────────────────────
_UCITS_SAMPLES = [
    {
        "id": "SYNTHETIC_UCITS_ES",
        "text": (
            "Datos fundamentales para el inversor\n"
            "Directiva UCITS.\n"
            "Gastos corrientes: 0,85%\n"
            "Comisión de gestión: 0,65%\n"
        ),
        "expected_oc": 0.85,
    },
    {
        "id": "SYNTHETIC_UCITS_EN",
        "text": (
            "Key Investor Information Document\n"
            "UCITS directive.\n"
            "Ongoing charges: 1.20%\n"
            "Entry charge: 0.00%   Exit charge: 0.00%   Ongoing charges: 1.20%\n"
        ),
        "expected_oc": 1.20,
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────

def _extract_text(pdf_path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as pdf:
            return '\n'.join(p.extract_text() or '' for p in pdf.pages)
    except Exception as e:
        return f"[ERROR: {e}]"


def _fmt(v, d=4):
    if v is None:
        return "—"
    return f"{v:.{d}f}" if isinstance(v, float) else str(v)


# ── Ejecución ─────────────────────────────────────────────────────────────

def run_smoke():
    results = []
    errors  = []

    print("\n" + "=" * 72)
    print("SMOKE TEST — Sprint 2 extractores de costes")
    print(f"PDF_DIR: {PDF_DIR}")
    print("=" * 72)

    # ── PARTE 1: PDFs PRIIPs ──────────────────────────────────────────────
    print(f"\n[1/2] PDFs PRIIPs ({len(_SAMPLE_ISINS)} fondos)\n")
    print(f"  {'ISIN':<16} {'KID_Format':<14} {'Quality':<16} "
          f"{'RHP':>5} {'Mgmt%':>7} {'ACI_RHP%':>9} {'Rows':>5}")
    print("  " + "-" * 72)

    for isin in _SAMPLE_ISINS:
        pdf_path = PDF_DIR / f"{isin}.pdf"
        if not pdf_path.exists():
            msg = f"PDF no encontrado: {pdf_path}"
            print(f"  ⚠ {isin:<14} {msg}")
            errors.append(msg)
            continue

        text = _extract_text(pdf_path)
        if text.startswith("[ERROR"):
            print(f"  ⚠ {isin:<14} {text}")
            errors.append(f"{isin}: {text}")
            continue

        try:
            result = extract_priips_costs(text=text, isin=isin, existing_oc=None)
        except Exception as e:
            print(f"  ❌ {isin:<14} EXCEPCIÓN: {e}")
            errors.append(f"{isin}: excepción: {e}")
            continue

        fmt     = result.get('KID_Format', '—')
        quality = result.get('Cost_Extraction_Quality', '—')
        rhp     = _fmt(result.get('Cost_RHP_Years'), 1)
        mgmt    = _fmt(result.get('Management_Fee_Pct'))
        aci_rhp = _fmt(result.get('ACI_RHP'))
        rows    = len(result.get('_cost_schedule_rows', []))
        ok      = fmt == 'PRIIPS_KID' and quality not in ('NONE', None)
        marker  = "✓" if ok else "⚠"

        print(f"  {marker} {isin:<14} {fmt:<14} {quality:<16} "
              f"{rhp:>5} {mgmt:>7} {aci_rhp:>9} {rows:>5}")

        results.append({'isin': isin, 'quality': quality, 'ok': ok, 'rows': rows})
        if not ok:
            errors.append(f"{isin}: KID_Format={fmt}, Quality={quality}")

    # ── PARTE 2: UCITS sintéticos ─────────────────────────────────────────
    print(f"\n[2/2] UCITS sintéticos ({len(_UCITS_SAMPLES)} muestras)\n")
    print(f"  {'ID':<24} {'KID_Format':<14} {'Quality':<8} "
          f"{'OC%':>6} {'Expected':>8} {'Match':>6}")
    print("  " + "-" * 68)

    for sample in _UCITS_SAMPLES:
        sid = sample['id']
        try:
            result = extract_ucits_costs(text=sample['text'], isin=sid, existing_oc=None)
        except Exception as e:
            print(f"  ❌ {sid:<22} EXCEPCIÓN: {e}")
            errors.append(f"{sid}: excepción: {e}")
            continue

        fmt     = result.get('KID_Format', '—')
        quality = result.get('Cost_Extraction_Quality', '—')
        oc      = result.get('Ongoing_Charge_Recurrent')
        exp     = sample['expected_oc']
        match   = oc is not None and abs(oc - exp) < 0.01
        ok      = fmt == 'UCITS_KIID' and quality == 'HIGH' and match
        marker  = "✓" if ok else "⚠"

        print(f"  {marker} {sid:<22} {fmt:<14} {quality:<8} "
              f"{_fmt(oc, 2):>6} {exp:>8.2f} {'OK' if match else 'FAIL':>6}")

        results.append({'isin': sid, 'quality': quality, 'ok': ok, 'rows': len(result.get('_cost_schedule_rows', []))})
        if not ok:
            errors.append(f"{sid}: KID_Format={fmt}, Quality={quality}, OC={oc}")

    # ── RESUMEN ───────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    from collections import Counter
    total    = len(results)
    ok_count = sum(1 for r in results if r['ok'])
    qdist    = Counter(r['quality'] for r in results)

    print(f"\n  Fondos evaluados: {total}  |  OK: {ok_count}  |  Incidencias: {total - ok_count}")
    print(f"\n  Distribución Cost_Extraction_Quality:")
    for q in ['HIGH', 'MEDIUM_CROSS', 'MEDIUM_EUR', 'MEDIUM_PCT', 'LOW', 'NONE', '—']:
        n = qdist.get(q, 0)
        if n:
            print(f"    {q:<16} {n:>3}  {'█' * n}")

    if errors:
        print(f"\n  ⚠ INCIDENCIAS ({len(errors)}):")
        for err in errors:
            print(f"    - {err}")
        print(f"\n  ➡ Revisar antes de ejecutar el pipeline completo.")
    else:
        print(f"\n  ✓ Sin incidencias.")
        print(f"    Ejecutar: discoverAllFunds.bat")

    print()
    return len(errors) == 0


if __name__ == "__main__":
    success = run_smoke()
    sys.exit(0 if success else 1)
