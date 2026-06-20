# -*- coding: utf-8 -*-
"""
dla2_ocr_fallback.py  v1.0  -- BL-DLA-2  vía OCR para KIIDs SIN capa de texto

Contexto
--------
El triaje de BOTH_FAIL reveló que ~86/110 fondos son KIIDs PRIIPS perfectamente
estructurados pero guardados como IMAGEN (0 caracteres extraíbles). Las vías
"ruled" (capa de texto) y "bands-X" (coordenadas) leen ambas la capa de texto de
pdfplumber, que en estos ficheros está vacía -> BOTH_FAIL.

Solución (probada): rasterizar la(s) página(s) de costes con PyMuPDF, pasar
Tesseract (spa) y alimentar el texto OCR al MISMO parser de la vía ruled
(_oc_from_lines de dla2_dual_strategy_compare v2.7). Sin lógica de parseo nueva.

Esta vía es de FUENTE ÚNICA (una sola imagen): el valor recuperado NO es un
AGREE cross-validado entre dos métodos independientes. El arbitraje lo etiqueta
como OCR_RECOVERED. Como señal de confianza interna, se deriva el OC por DOS
caminos del MISMO OCR -> (a) los % de las etiquetas y (b) los importes en EUR
sobre el ejemplo de inversión; si concuerdan dentro de tolerancia, confidence=HIGH.

Requisitos
----------
  pip install pytesseract pymupdf pillow
  + binario Tesseract con idioma spa (Windows: instalar Tesseract-OCR y el
    paquete de idioma 'spa'; asegurar que tesseract.exe está en PATH o fijar
    pytesseract.pytesseract.tesseract_cmd).

Uso directo (diagnóstico):
  python dla2_ocr_fallback.py "C:\\desarrollo\\fondos\\kiids\\LU0293294277.pdf"
"""
from __future__ import annotations
import os
import re
import sys
import unicodedata

import fitz                       # PyMuPDF
import pytesseract
from PIL import Image

# La vía OCR reutiliza el parser de la vía ruled. Ambos módulos viven en
# scripts/diag/. Aseguramos que ese directorio (el de ESTE fichero) esté en
# sys.path para que el import funcione tanto con `python scripts\diag\...py`
# como con `python -m scripts.diag.dla2_ocr_fallback` o importado desde el harness.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Reutiliza EXACTAMENTE el parser y los patrones de la vía ruled (lockstep).
from dla2_dual_strategy_compare import (  # noqa: E402
    _oc_from_lines, _R_TXT_GEST, _R_TXT_OPER)

# Si en Windows tesseract.exe no está en PATH, descomentar y ajustar:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Importe del ejemplo de inversión PRIIPS (para el cross-check en EUR).
_PAT_EXAMPLE = re.compile(r'(?:invierten|inversi[oó]n|investment)[^\d]{0,40}'
                          r'([\d][\d.\s]{3,})\s*(?:EUR|USD|€|\$)', re.I)
_PAT_EUR = re.compile(r'(\d[\d.\s]{1,7})\s*(?:EUR|USD|€|\$)')
_DEF_LANGS = "spa+eng+fra"


def _resolve_langs(requested: str) -> str:
    """Intersecta los idiomas pedidos con los realmente instalados en Tesseract.
    Evita que falte un traineddata (p.ej. 'fra') y reviente toda la pasada de 86
    fondos. Prioriza 'spa' (el corpus DB-España es casi todo español). Si no hay
    ninguno de los pedidos, cae al primero disponible (o 'eng')."""
    try:
        available = set(pytesseract.get_languages(config=""))
    except Exception:                                       # noqa: BLE001
        return requested                                    # que decida Tesseract
    keep = [l for l in requested.split("+") if l in available]
    if not keep:
        keep = ["spa"] if "spa" in available else (
            ["eng"] if "eng" in available else sorted(available)[:1])
    return "+".join(keep) if keep else requested


def pdf_needs_ocr(pdf, max_pages: int = 3, min_chars: int = 150) -> bool:
    """True si el PDF (objeto pdfplumber) NO tiene capa de texto útil en las
    primeras max_pages -> candidato a OCR. Pensado para llamarse SOLO cuando
    ruled y bands-X ya han fallado, evitando OCR-ear los ~3000 PDFs con texto."""
    try:
        return sum(len(p.chars) for p in pdf.pages[:max_pages]) < min_chars
    except Exception:                                       # noqa: BLE001
        return False


def _num(s: str):
    s = s.strip().replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _ocr_lines(path: str, max_pages: int, dpi: int, langs: str):
    """Rasteriza + OCR las primeras max_pages páginas y concatena todas las
    líneas. Se OCR-ean todas (no solo la que contenga la cabecera) porque varias
    gestoras parten la sección: cabecera al pie de una página y tabla en la
    siguiente (p.ej. Rothschild). Esta vía solo corre en PDFs sin capa de texto,
    así que el coste de OCR-ear 2-3 páginas es asumible."""
    doc = fitz.open(path)
    langs = _resolve_langs(langs)
    lines = []
    for i in range(min(len(doc), max_pages)):
        pix = doc[i].get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        t = unicodedata.normalize("NFC", pytesseract.image_to_string(img, lang=langs))
        lines.extend(t.split("\n"))
    return [l for l in lines if l.strip()]


def _eur_crosscheck(lines):
    """Deriva OC% a partir de importes EUR / ejemplo de inversión (señal
    independiente del mismo OCR). Devuelve OC% aproximado o None. Tolerante a
    redondeo a euros enteros."""
    base = None
    for l in lines:
        m = _PAT_EXAMPLE.search(l)
        if m:
            base = _num(m.group(1))
            if base and base >= 1000:
                break
    if not base:
        return None
    tot = 0.0
    found = False
    for i, l in enumerate(lines):
        if _R_TXT_GEST.search(l) or _R_TXT_OPER.search(l):
            for j in range(i, min(i + 3, len(lines))):
                m = _PAT_EUR.search(lines[j])
                if m:
                    v = _num(m.group(1))
                    if v is not None and 0 < v < base:
                        tot += v / base * 100.0
                        found = True
                        break
    return round(tot, 4) if found else None


def extract_ocr_from_pdf(path: str, max_pages: int = 3, dpi: int = 300,
                         langs: str = _DEF_LANGS) -> dict:
    """Recupera el OC de un KIID sin capa de texto vía OCR. Devuelve dict con
    misma forma que ruled/bands-X (+ campos OCR) o {} si no recupera nada.
      {oc, breakdown, n_components, source='ocr', confidence, oc_eur_check}
    """
    try:
        lines = _ocr_lines(path, max_pages, dpi, langs)
    except Exception as e:                                  # noqa: BLE001
        return {"source": "ocr", "error": f"{type(e).__name__}: {e}"}

    gest, oper = _oc_from_lines(lines)
    present = [v for v in (gest, oper) if v is not None]
    if not present:
        return {}

    # v2.6 guard: operación-sola (gestión ausente) = fallo de parseo -> no OC.
    if gest is None and oper is not None:
        return {}

    oc = round(sum(present), 4)
    eur = _eur_crosscheck(lines)
    # confianza: HIGH si el % y el EUR concuerdan (~redondeo a euros, tol 0.12)
    conf = "LOW"
    if eur is not None and abs(eur - oc) <= 0.12:
        conf = "HIGH"
    elif len(present) >= 2:
        conf = "MED"
    return {
        "oc": oc,
        "breakdown": "+".join(str(v) for v in present),
        "n_components": len(present),
        "source": "ocr",
        "confidence": conf,
        "oc_eur_check": eur,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("uso: python dla2_ocr_fallback.py <ruta_pdf> [dpi]")
    p = sys.argv[1]
    d = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    print(extract_ocr_from_pdf(p, dpi=d))
