# core/cost_arbitration.py  — v1  (INTEGRATED_SPEC_v20_v2 §4.4 — Job B)
# -*- coding: utf-8 -*-
"""
Arbitración de coste per-componente para el flujo de producción.

Promueve la lógica dual-strategy validada en scripts/diag
(dla2_dual_strategy_compare) a un callable puro, sin efectos secundarios
(ni CSV ni BD), con UN solo `pdfplumber.open` por fondo. Replica el modelo
de datos de SRRI (visual / textual / validation_status) pero por componente.

Contrato de retorno (spec §4.4):
    {
      'oc':   float | None,                      # OC consolidado (mejor evidencia)
      'mgmt': {'bandsx': float|None, 'ruled': float|None, 'verdict': str|None},
      'oper': {'bandsx': float|None, 'ruled': float|None, 'verdict': str|None},
      'table_text': str | None,                  # tabla de mayor fidelidad (o None)
    }

Veredicto por componente (config.COST_ARBITRATION_VALUES):
    ambos concuerdan        -> AGREE
    solo una estrategia      -> ONLY_BANDS_X / ONLY_RULED
    ambas discrepan          -> CONFLICT
    ninguna tabla, OCR salva -> OCR_RECOVERED
    ninguna + OCR falla      -> BOTH_FAIL
    nunca arbitrado          -> None  (lo decide el writer, no esta función)

La distinción BOTH_FAIL (intentado y fallido) vs None (no intentado) es más
rica que el NOT_AVAILABLE de SRRI y la preserva el writer vía COALESCE (§4.3).

Concordancia: classify_utils.cost_values_agree (tolerancia híbrida ATOL+RTOL,
config v20) — fuente única, sustituye al _TOL=0.011 fijo del prototipo.
"""

from __future__ import annotations

import io as _io
import re as _re
from typing import Optional, Dict, Any

# ── pdfplumber (motor primario; mismo que el prototipo validado) ──────────────
try:
    import pdfplumber  # type: ignore
except Exception:       # pragma: no cover
    pdfplumber = None


# ── Comparador compartido (R-1, DRY) ──────────────────────────────────────────
def _import_cost_values_agree():
    try:
        from classify_utils import cost_values_agree
        return cost_values_agree
    except ImportError:  # pragma: no cover
        from core.classify_utils import cost_values_agree
        return cost_values_agree


# ── Primitivas de extracción validadas (DRY: NO se duplican) ──────────────────
#   bands-X : extract_from_open_pdf  (+ claves _OC_GESTION_VAL / _OC_OPERACION_VAL)
#   ruled   : extract_ruled_from_pdf, _recover_oc_text  (gest/oper etiquetados)
#   OCR     : extract_ocr_from_pdf, pdf_needs_ocr        (solo en BOTH_FAIL)
# Imports diferidos y defensivos: los módulos pueden vivir en core/ o scripts/diag
# y el sys.path varía según el lanzador. Se resuelven en la 1ª llamada.
_PRIMS: Dict[str, Any] = {}


def _load_primitives() -> Dict[str, Any]:
    if _PRIMS:
        return _PRIMS

    def _imp(modnames, attrs):
        last = None
        for m in modnames:
            try:
                mod = __import__(m, fromlist=attrs)
                return tuple(getattr(mod, a) for a in attrs)
            except Exception as e:  # ImportError o AttributeError
                last = e
        raise ImportError(f"No se pudo importar {attrs} desde {modnames}: {last}")

    (extract_from_open_pdf,) = _imp(
        ("dla2_xband_prototype", "core.dla2_xband_prototype",
         "scripts.diag.dla2_xband_prototype"),
        ("extract_from_open_pdf",))
    (extract_ruled_from_pdf, _recover_oc_text) = _imp(
        ("dla2_dual_strategy_compare", "core.dla2_dual_strategy_compare",
         "scripts.diag.dla2_dual_strategy_compare"),
        ("extract_ruled_from_pdf", "_recover_oc_text"))
    # OCR es opcional: si no está disponible, BOTH_FAIL se reporta sin recuperar.
    try:
        (extract_ocr_from_pdf, pdf_needs_ocr) = _imp(
            ("dla2_ocr_fallback", "core.dla2_ocr_fallback",
             "scripts.diag.dla2_ocr_fallback"),
            ("extract_ocr_from_pdf", "pdf_needs_ocr"))
    except ImportError:
        extract_ocr_from_pdf = pdf_needs_ocr = None

    _PRIMS.update(
        extract_from_open_pdf=extract_from_open_pdf,
        extract_ruled_from_pdf=extract_ruled_from_pdf,
        _recover_oc_text=_recover_oc_text,
        extract_ocr_from_pdf=extract_ocr_from_pdf,
        pdf_needs_ocr=pdf_needs_ocr,
        cost_values_agree=_import_cost_values_agree(),
    )
    return _PRIMS


# ── Helpers de parseo ─────────────────────────────────────────────────────────
# Mismo patrón % que el prototipo (bands-X / ruled): "1,73 %" | "1.73%" | "0 %".
_PCT = _re.compile(r'(\d{1,3}[,\.]\d{1,4})\s*%|\b(0)\s*[%％]')
# breakdown ruled etiquetado: "g1.24+o0.11" -> {'g':1.24,'o':0.11}
_BD_LABELLED = _re.compile(r'([go])(\d{1,3}\.\d{1,4})')


def _pct_from_str(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    m = _PCT.search(s)
    if not m:
        return None
    return float(m.group(1).replace(",", ".")) if m.group(1) else 0.0


def _bandsx_components(bx_res: dict) -> tuple[Optional[float], Optional[float]]:
    """(gestión, operación) de bands-X usando las claves etiquetadas aditivas."""
    if not bx_res:
        return None, None
    g = bx_res.get("_OC_GESTION_VAL")
    o = bx_res.get("_OC_OPERACION_VAL")
    return (float(g) if g is not None else None,
            float(o) if o is not None else None)


def _ruled_components(rl_res: dict, pdf_obj) -> tuple[Optional[float], Optional[float]]:
    """(gestión, operación) de la estrategia ruled.

    1) Si la tabla ruled etiquetó componentes (source='ruled', breakdown 'g..+o..')
       se parsean directamente.
    2) En otro caso se usa el fallback texto-anclado de la propia estrategia ruled
       (_recover_oc_text -> (gest, oper) etiquetados por la etiqueta de componente).
    """
    if rl_res and rl_res.get("source") == "ruled":
        bd = rl_res.get("breakdown") or ""
        found = dict(_BD_LABELLED.findall(bd))
        if found:
            g = float(found["g"]) if "g" in found else None
            o = float(found["o"]) if "o" in found else None
            if g is not None or o is not None:
                return g, o
    # Fallback texto-anclado (etiquetado) — misma primitiva validada que usa la
    # estrategia ruled internamente. No reabre el PDF (recibe el objeto abierto).
    prims = _load_primitives()
    try:
        g, o = prims["_recover_oc_text"](pdf_obj, max_pages=3)
        return g, o
    except Exception:
        return None, None


def _verdict(bx: Optional[float], rl: Optional[float],
             ocr_recovered: bool, agree) -> Optional[str]:
    """Veredicto de un componente. Replica classify() del prototipo pero con el
    comparador híbrido compartido y el estado OCR_RECOVERED / BOTH_FAIL."""
    bx_ok = bx is not None
    rl_ok = rl is not None
    if not bx_ok and not rl_ok:
        return "OCR_RECOVERED" if ocr_recovered else "BOTH_FAIL"
    if bx_ok and not rl_ok:
        return "ONLY_BANDS_X"
    if rl_ok and not bx_ok:
        return "ONLY_RULED"
    return "AGREE" if agree(bx, rl) else "CONFLICT"


# ── Callable principal ────────────────────────────────────────────────────────
def arbitrate_costs_from_pdf(pdf_bytes: bytes) -> dict:
    """Arbitra coste (OC consolidado + per-componente) sobre el binario del KIID.

    Un solo `pdfplumber.open` por fondo (bands-X y ruled comparten el PDF
    abierto). Sin efectos secundarios. Devuelve el contrato §4.4. Cualquier
    fallo se degrada limpiamente a veredictos None / BOTH_FAIL (nunca propaga
    una excepción al pipeline — defensive error handling).
    """
    empty = {
        "oc": None,
        "mgmt": {"bandsx": None, "ruled": None, "verdict": None},
        "oper": {"bandsx": None, "ruled": None, "verdict": None},
        "table_text": None,
    }
    if not pdf_bytes or pdfplumber is None:
        return empty

    try:
        prims = _load_primitives()
    except Exception:
        return empty

    agree = prims["cost_values_agree"]
    extract_from_open_pdf = prims["extract_from_open_pdf"]
    extract_ruled_from_pdf = prims["extract_ruled_from_pdf"]
    extract_ocr_from_pdf = prims["extract_ocr_from_pdf"]
    pdf_needs_ocr = prims["pdf_needs_ocr"]

    bx_res: dict = {}
    rl_res: dict = {}
    ocr_needed = False

    try:
        with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf_obj:
            try:
                bx_res = extract_from_open_pdf(pdf_obj, debug=False) or {}
            except Exception:
                bx_res = {}
            try:
                rl_res = extract_ruled_from_pdf(pdf_obj, max_pages=3) or {}
            except Exception:
                rl_res = {}
            try:
                ocr_needed = bool(pdf_needs_ocr(pdf_obj)) if pdf_needs_ocr else False
            except Exception:
                ocr_needed = False

            # Per-componente (mientras el PDF sigue abierto)
            bx_g, bx_o = _bandsx_components(bx_res)
            rl_g, rl_o = _ruled_components(rl_res, pdf_obj)
    except Exception:
        return empty

    # OC consolidado por estrategia
    bx_oc = _pct_from_str(bx_res.get("Costes corrientes")) if bx_res else None
    rl_oc = rl_res.get("oc") if rl_res else None

    # OCR: solo si ambas estrategias fallaron el OC consolidado y no hay capa de
    # texto (igual gating que el prototipo validado). Recupera OC consolidado.
    ocr_oc = None
    if bx_oc is None and rl_oc is None and ocr_needed and extract_ocr_from_pdf:
        try:
            ocr_res = extract_ocr_from_pdf_bytes(pdf_bytes, extract_ocr_from_pdf)
            ocr_oc = ocr_res.get("oc") if ocr_res else None
        except Exception:
            ocr_oc = None
    ocr_recovered = ocr_oc is not None

    # OC consolidado final: preferir acuerdo; si solo una estrategia, esa; si
    # discrepan, conservar bands-X (criterio del prototipo: no inventar); OCR si
    # ambas fallaron.
    if bx_oc is not None and rl_oc is not None:
        oc = bx_oc if agree(bx_oc, rl_oc) else bx_oc
    elif bx_oc is not None:
        oc = bx_oc
    elif rl_oc is not None:
        oc = rl_oc
    else:
        oc = ocr_oc

    return {
        "oc": oc,
        "mgmt": {
            "bandsx": bx_g, "ruled": rl_g,
            "verdict": _verdict(bx_g, rl_g, ocr_recovered, agree),
        },
        "oper": {
            "bandsx": bx_o, "ruled": rl_o,
            "verdict": _verdict(bx_o, rl_o, ocr_recovered, agree),
        },
        # table_text de mayor fidelidad: pendiente de integrar dla_table_serializer
        # en la vía de arbitración (no regresa nada -> el writer no sobrescribe el
        # DLA2_Table_Text ya producido por io.py). Follow-up documentado en handoff.
        "table_text": None,
    }


def extract_ocr_from_pdf_bytes(pdf_bytes: bytes, _ocr_fn) -> dict:
    """extract_ocr_from_pdf del prototipo recibe una RUTA. Aquí materializamos
    el binario a un temporal y delegamos, para mantener el contrato sin-ruta del
    callable de producción. Best-effort: si Tesseract no está, devuelve {}."""
    import tempfile
    import os as _os
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(pdf_bytes)
            tmp = fh.name
        return _ocr_fn(tmp) or {}
    except Exception:
        return {}
    finally:
        if tmp:
            try:
                _os.unlink(tmp)
            except Exception:
                pass
