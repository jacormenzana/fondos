# -*- coding: utf-8 -*-
"""
both_fail_triage.py  -- BL-DLA-2  clasificacion del bucket BOTH_FAIL

Clasifica cada PDF de los fondos en arbitration=BOTH_FAIL en:
  WRONG_DOC        -> no es un KIID (folleto/estatutos): muchas paginas
  NO_TEXT_OCR      -> sin capa de texto util (glifos/imagen): necesita OCR
  OLD_UCITS_KIID   -> KIID antiguo pre-PRIIPs ("Gastos corrientes: X%")
  HAS_TEXT_OTHER   -> con texto, layout PRIIPS no resuelto (candidato a fix)
  ERROR:<tipo>     -> el PDF no se pudo abrir/leer

Uso (1 linea, consola Windows):
  python both_fail_triage.py --pdfdir "C:\\desarrollo\\fondos\\kiids"

Opcionales:
  --csv  ruta del CSV de comparacion (def: C:\\desarrollo\\fondos\\dla2_dual_strategy_compare.csv)
  --out  ruta del CSV de salida       (def: C:\\desarrollo\\fondos\\both_fail_triage.csv)
  --glob patron de nombre de fichero  (def: {isin}.pdf)  p.ej. "KID_{isin}_es.pdf"

Requiere: pip install pdfplumber
"""
from __future__ import annotations
import argparse, csv, sys, unicodedata, re
from collections import Counter
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("[FATAL] Falta pdfplumber. Instala con: pip install pdfplumber")

_OLDK = re.compile(r'gastos\s+corrientes', re.I)


def classify(path: Path):
    """Devuelve (clase, n_paginas, n_chars)."""
    with pdfplumber.open(str(path)) as pdf:
        npages = len(pdf.pages)
        sample = pdf.pages[:3]
        nchars = sum(len(p.chars) for p in sample)
        txt = unicodedata.normalize(
            "NFC", " ".join((p.extract_text() or "") for p in sample))
    if npages > 8:
        return "WRONG_DOC", npages, nchars
    if nchars < 150:
        return "NO_TEXT_OCR", npages, nchars
    if _OLDK.search(txt):
        return "OLD_UCITS_KIID", npages, nchars
    return "HAS_TEXT_OTHER", npages, nchars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=r"C:\desarrollo\fondos\dla2_dual_strategy_compare.csv")
    ap.add_argument("--pdfdir", required=True, help="carpeta con los PDF de KIID")
    ap.add_argument("--out", default=r"C:\desarrollo\fondos\both_fail_triage.csv")
    ap.add_argument("--glob", default="{isin}.pdf", help="patron de nombre, usa {isin}")
    args = ap.parse_args()

    pdfdir = Path(args.pdfdir)
    if not pdfdir.is_dir():
        sys.exit(f"[FATAL] No existe la carpeta de PDFs: {pdfdir}")

    with open(args.csv, encoding="utf-8") as fh:
        isins = [r["ISIN"] for r in csv.DictReader(fh)
                 if r.get("arbitration") == "BOTH_FAIL"]
    if not isins:
        sys.exit("[FATAL] No hay filas BOTH_FAIL en el CSV (revisa --csv).")

    print(f"BOTH_FAIL a clasificar: {len(isins)}  | PDFs en: {pdfdir}\n")
    rows, counts = [], Counter()
    for n, isin in enumerate(isins, 1):
        path = pdfdir / args.glob.format(isin=isin)
        if not path.exists():
            cls, npages, nchars = "ERROR:NOT_FOUND", "", ""
        else:
            try:
                cls, npages, nchars = classify(path)
            except Exception as e:                       # noqa: BLE001
                cls, npages, nchars = f"ERROR:{type(e).__name__}", "", ""
        counts[cls.split(':')[0]] += 1
        rows.append((isin, cls, npages, nchars))
        if n % 20 == 0:
            print(f"  [{n}/{len(isins)}] ...")

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(("ISIN", "class", "pages", "chars_p1_3"))
        w.writerows(rows)

    print("\n===== RESUMEN BOTH_FAIL =====")
    for cls, c in counts.most_common():
        print(f"  {cls:16}: {c}")
    print(f"\n  Detalle por ISIN -> {args.out}")


if __name__ == "__main__":
    main()
