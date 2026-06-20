# -*- coding: utf-8 -*-
"""
scripts/diag/dla2_serializer_audit.py  v1.2  -- BL-DLA-2-AUDIT
=============================================================
Auditoría de la tasa de éxito REAL del serializador de tablas DLA-2.

PROPÓSITO (responsabilidad única):
    Ejecutar core.dla_table_serializer.serialize_tables() contra los
    pdf_bytes REALES de cada fondo (KIID_Class=1) y clasificar el
    resultado en un veredicto, para:
      1. Medir qué porcentaje del corpus produce una tabla de costes útil.
      2. MARCAR los ISINs donde el serializador falla (strategy=none,
         cat2a=0) o produce salida incompleta/incorrecta (solo EUR, sin OC),
         de modo que esos KIID puedan recogerse como muestra dirigida.

    NO decide Go/No-Go, NO clasifica Cat.1/2/3 sobre texto, NO toca la BD
    (solo lee la lista de ISINs). Esa es responsabilidad de
    dla2_decision_diag.py — este script es complementario e independiente.

DIFERENCIA CLAVE CON dla2_decision_diag.py:
    decision_diag mide PREVALENCIA DE SEÑAL textual (¿hay tabla en el Raw?).
    Este script mide ÉXITO DE EXTRACCIÓN (¿el serializador saca bien la tabla?).
    Son preguntas distintas; la segunda requiere los pdf_bytes, no solo el Raw.

OBTENCIÓN DE PDFs:
    Reutiliza io.find_kiid_links_from_excel() + io.download_pdf().
    Descarga SIEMPRE (no usa caché por hash): la auditoría necesita los bytes
    reales, independientemente del KIID_Status. NO requiere FORCE_REFRESH ni
    modificar la BD. Alternativamente, --from-pdf-dir lee PDFs de una carpeta
    local (modo offline / repositorio local futuro).

SALIDAS (propias, no comparte con decision_diag):
    - CSV:  dla2_serializer_audit.csv   (1 fila por ISIN; reanudable)
    - Log:  dla2_serializer_audit.log   (resumen agregado + listas de fallos)

REANUDABLE:
    El CSV se escribe incrementalmente. Al relanzar:
      - Los ISINs con veredicto DEFINITIVO (OK_*, FAIL_NONE, WARN_*, ERROR) se
        saltan: no se re-descargan.
      - Los ISINs con veredicto TRANSITORIO (FAIL_NO_URL, FAIL_DOWNLOAD) se
        REINTENTAN automáticamente, y su fila vieja se purga del CSV para no
        duplicar. Así, un corte de red a mitad de una pasada de ~3.200 fondos
        se recupera con un simple relanzamiento, sin perder lo ya descargado
        ni necesitar --restart.
    Usa --restart solo para descartar TODO y empezar de cero.

USO:
    # Pasada de prueba (descarga 30 fondos al azar):
    python dla2_serializer_audit.py --limit 30 --seed 42

    # Corpus completo (todos los KIID_Class=1):
    python dla2_serializer_audit.py

    # Validación offline contra una carpeta de PDFs:
    python dla2_serializer_audit.py --from-pdf-dir ./muestra_pdfs

    # Reanudar tras interrupción (por defecto salta lo ya hecho):
    python dla2_serializer_audit.py            # continúa
    python dla2_serializer_audit.py --restart  # empieza de cero

VEREDICTOS:
    OK_PDFPLUMBER  strategy=pdfplumber, cat2a>=1, >=1 coste con valor
    OK_HEURISTIC   strategy=heuristic_lines, cat2a>=1, >=1 coste con valor
    WARN_EUR_ONLY  extrae costes pero solo en importes EUR (ningún %)
    WARN_NO_OC     extrae tabla pero sin "Costes corrientes" (OC ausente)
    FAIL_NONE      strategy=none o cat2a=0 -> NO extrae tabla de costes
    FAIL_NO_URL    sin URL de KIID en el Excel maestro
    FAIL_DOWNLOAD  error de descarga / PDF demasiado grande
    ERROR          excepción no controlada en serialize_tables()

    Candidatos a muestra dirigida: FAIL_NONE, WARN_EUR_ONLY, WARN_NO_OC, ERROR.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
import traceback
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Resolución de raíz del proyecto (idéntica heurística que dla2_decision_diag.py)
# ──────────────────────────────────────────────────────────────────────────────

def _find_project_root(start: Path) -> Path:
    candidate = start.resolve()
    for _ in range(8):
        if (candidate / "shared" / "db.py").exists():
            return candidate
        if (candidate / "shared").is_dir():
            return candidate
        if (candidate / "proyecto1").is_dir() and (candidate / "proyecto2").is_dir():
            return candidate
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return start.resolve().parent.parent


_THIS_FILE         = Path(__file__).resolve()
_PROJECT_ROOT_AUTO = _find_project_root(_THIS_FILE.parent)


def _setup_path(explicit_root: Optional[str] = None) -> Path:
    """
    Añade al sys.path la raíz del proyecto (para 'shared.*') y proyecto1/
    (para el paquete 'core.*', que vive en proyecto1/core/).

    Nota: dla2_decision_diag.py solo necesita la raíz porque accede a la BD
    vía shared.db y carga el resto por ruta de fichero. Este script SÍ importa
    core.io (download_pdf, find_kiid_links_from_excel), de modo que proyecto1/
    debe estar en el path para que 'core' sea importable como paquete.
    """
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # proyecto1/ contiene el paquete 'core'. Lo añadimos si existe.
    p1 = root / "proyecto1"
    if p1.is_dir() and str(p1) not in sys.path:
        sys.path.insert(0, str(p1))
    return root


# ──────────────────────────────────────────────────────────────────────────────
# Logger dual: consola + buffer (volcado a fichero al final)
# Mismo patrón que dla2_decision_diag._DualLogger (consistencia de salida).
# ──────────────────────────────────────────────────────────────────────────────

class _DualLogger:
    def __init__(self):
        self._buf = StringIO()

    def _write(self, text: str = ""):
        print(text)
        self._buf.write(text + "\n")

    def section(self, title: str):
        sep = "=" * 70
        self._write(f"\n{sep}")
        self._write(f"  {title}")
        self._write(sep)

    def subsection(self, title: str):
        self._write(f"\n  -- {title} --")

    def row(self, label: str, value, width: int = 40):
        self._write(f"  {label:<{width}}: {value}")

    def table(self, headers, rows, indent: int = 2):
        if not rows:
            self._write(" " * indent + "(sin resultados)")
            return
        widths = [len(h) for h in headers]
        for r in rows:
            for i, v in enumerate(r):
                widths[i] = max(widths[i], len(str(v) if v is not None else "NULL"))
        pad = " " * indent
        header_line = pad + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        self._write(header_line)
        self._write(pad + "-" * (len(header_line) - indent))
        for r in rows:
            self._write(pad + "  ".join(
                (str(v) if v is not None else "NULL").ljust(widths[i])
                for i, v in enumerate(r)
            ))

    def warning(self, msg: str):
        self._write(f"  /!\\ {msg}")

    def blank(self):
        self._write("")

    def flush_to_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._buf.getvalue(), encoding="utf-8")
        print(f"\n[LOG] Salida guardada en: {path}")


log = _DualLogger()


# ──────────────────────────────────────────────────────────────────────────────
# Esquema del CSV de auditoría
# ──────────────────────────────────────────────────────────────────────────────

_CSV_FIELDS = [
    "ISIN",
    "kiid_url",
    "n_pages",
    "strategy",            # pdfplumber | heuristic_lines | none
    "fallback",            # 1 si usó heurística
    "rows_extracted",
    "cat1", "cat2a", "cat2b",
    "has_entry", "has_exit", "has_oc", "has_perf",
    # v1.2: valor CRUDO serializado por componente (string tal cual lo emitió
    # el serializador, p.ej. "Hasta 500 EUR" / "0,74 %"). Permite al diagnóstico
    # comparar lo que DLA2 extrajo contra el valor de referencia en BD.
    "val_entry", "val_exit", "val_oc", "val_perf",
    "value_format",        # PCT | EUR | MIXED | NONE
    "verdict",
    "failure_reason",
    "duration_ms",
]


# ──────────────────────────────────────────────────────────────────────────────
# Análisis del texto serializado: detección de componentes y formato de valor
# Las etiquetas canónicas las fija dla_table_serializer._serialize_cat2a:
#   "Costes de entrada", "Costes de salida", "Costes corrientes", "Comisión de éxito"
# ──────────────────────────────────────────────────────────────────────────────

_LBL_ENTRY = "Costes de entrada"
_LBL_EXIT  = "Costes de salida"
_LBL_OC    = "Costes corrientes"
_LBL_PERF  = "Comisión de éxito"

# Un valor cuenta como "% real" si el componente trae un dígito seguido de %.
_PAT_PCT = re.compile(r"\d[\d.,]*\s*%")
# Un valor cuenta como "EUR/importe" si trae moneda o "X EUR/USD" sin %.
_PAT_CUR = re.compile(r"\d[\d.,]*\s*(?:EUR|USD|GBP|CHF|€|\$)", re.IGNORECASE)


def _component_lines(table_text: str) -> dict:
    """
    Devuelve dict {canonical_label: value_str} a partir del texto serializado.
    Solo considera las líneas 'Etiqueta: valor' de costes Cat.2A.
    """
    found = {}
    for line in table_text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        value = value.strip()
        for canon in (_LBL_ENTRY, _LBL_EXIT, _LBL_OC, _LBL_PERF):
            # Coincidencia exacta de etiqueta canónica al inicio de la línea.
            if label == canon:
                found[canon] = value
    return found


def _classify_value_format(components: dict) -> str:
    """
    PCT   — al menos un componente con % y ninguno solo-EUR
    EUR   — al menos un componente con importe de moneda y ningún %
    MIXED — conviven % y EUR
    NONE  — ningún valor numérico reconocible
    """
    has_pct = False
    has_eur = False
    for value in components.values():
        if _PAT_PCT.search(value):
            has_pct = True
        elif _PAT_CUR.search(value):
            has_eur = True
    if has_pct and has_eur:
        return "MIXED"
    if has_pct:
        return "PCT"
    if has_eur:
        return "EUR"
    return "NONE"


def _verdict(meta: dict, components: dict, value_format: str) -> tuple:
    """
    Aplica la taxonomía de veredictos. Devuelve (verdict, failure_reason).
    Solo se llama cuando serialize_tables() se ejecutó sin excepción.
    """
    cat2a = meta["tables_detected"].get("cat2a", 0)
    strategy = meta.get("strategy", "none")

    # Sin tabla de costes detectada → fallo duro.
    if strategy == "none" or cat2a == 0:
        return "FAIL_NONE", "serializer_no_cat2a"

    has_value = value_format in ("PCT", "EUR", "MIXED")
    if not has_value:
        # Detectó cat2a pero ningún valor numérico legible.
        return "FAIL_NONE", "cat2a_sin_valor"

    # A partir de aquí hay tabla con al menos un valor.
    has_oc = _LBL_OC in components

    if value_format == "EUR":
        # Caso Deutsche: importes en EUR sobre base 10.000, ningún %.
        return "WARN_EUR_ONLY", "valores_solo_EUR"

    if not has_oc:
        # Tiene % pero falta el componente clave (Costes corrientes).
        return "WARN_NO_OC", "sin_costes_corrientes"

    # Éxito: tabla con OC y al menos un %.
    if strategy == "pdfplumber":
        return "OK_PDFPLUMBER", ""
    return "OK_HEURISTIC", ""


# ──────────────────────────────────────────────────────────────────────────────
# Auditoría de un PDF (núcleo reutilizable: vale para descarga y para carpeta)
# ──────────────────────────────────────────────────────────────────────────────

def _audit_pdf_bytes(isin: str, kiid_url: str, pdf_bytes: bytes,
                     serialize_tables) -> dict:
    """
    Ejecuta serialize_tables sobre los bytes y construye la fila CSV.
    No descarga nada: recibe los bytes ya en memoria.
    """
    row = {k: "" for k in _CSV_FIELDS}
    row["ISIN"]     = isin
    row["kiid_url"] = kiid_url or ""

    t0 = time.time()
    try:
        table_text, meta = serialize_tables(pdf_bytes, debug=False)
    except Exception as e:
        row["verdict"]        = "ERROR"
        row["failure_reason"] = f"serialize_exception: {type(e).__name__}: {e}"
        row["duration_ms"]    = int((time.time() - t0) * 1000)
        return row

    components   = _component_lines(table_text)
    value_format = _classify_value_format(components)
    verdict, reason = _verdict(meta, components, value_format)

    td = meta.get("tables_detected", {})
    row.update({
        "n_pages":        meta.get("n_pages_scanned", 0),
        "strategy":       meta.get("strategy", "none"),
        "fallback":       1 if meta.get("fallback") else 0,
        "rows_extracted": meta.get("rows_extracted", 0),
        "cat1":           td.get("cat1", 0),
        "cat2a":          td.get("cat2a", 0),
        "cat2b":          td.get("cat2b", 0),
        "has_entry":      1 if _LBL_ENTRY in components else 0,
        "has_exit":       1 if _LBL_EXIT  in components else 0,
        "has_oc":         1 if _LBL_OC    in components else 0,
        "has_perf":       1 if _LBL_PERF  in components else 0,
        # v1.2: valor crudo serializado (vacío si el componente no se extrajo).
        "val_entry":      components.get(_LBL_ENTRY, ""),
        "val_exit":       components.get(_LBL_EXIT,  ""),
        "val_oc":         components.get(_LBL_OC,    ""),
        "val_perf":       components.get(_LBL_PERF,  ""),
        "value_format":   value_format,
        "verdict":        verdict,
        "failure_reason": reason,
        "duration_ms":    int((time.time() - t0) * 1000),
    })
    return row


# ──────────────────────────────────────────────────────────────────────────────
# Fuentes de ISINs / PDFs
# ──────────────────────────────────────────────────────────────────────────────

# Veredictos TRANSITORIOS: fallos de entorno (no del fondo ni del serializador).
# Al reanudar se REINTENTAN — no cuentan como "hechos". Sus filas viejas se
# purgan del CSV para que el reintento no genere duplicados de ISIN.
_TRANSIENT_VERDICTS = {"FAIL_NO_URL", "FAIL_DOWNLOAD"}


def _load_done_isins(csv_path: Path) -> set:
    """
    Devuelve los ISINs con veredicto DEFINITIVO (a saltar al reanudar) y,
    como efecto colateral, reescribe el CSV eliminando las filas con veredicto
    TRANSITORIO para que el reintento no produzca filas duplicadas.

    Definitivo  = OK_*, FAIL_NONE, WARN_*, ERROR  -> se respeta, se salta.
    Transitorio = FAIL_NO_URL, FAIL_DOWNLOAD       -> se purga, se reintenta.
    """
    done = set()
    if not csv_path.exists():
        return done

    try:
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)
    except Exception as e:
        log.warning(f"No se pudo leer CSV existente ({csv_path}): {e}. Se ignora.")
        return done

    kept_rows    = []   # filas definitivas que se conservan
    n_transient  = 0    # filas transitorias purgadas (a reintentar)
    for r in all_rows:
        isin = r.get("ISIN")
        if not isin:
            continue
        verdict = (r.get("verdict") or "").strip()
        if verdict in _TRANSIENT_VERDICTS:
            n_transient += 1            # se descarta de kept_rows → se reintentará
            continue
        kept_rows.append(r)
        done.add(isin)

    # Reescribir el CSV solo con las filas definitivas (purga las transitorias).
    if n_transient > 0:
        try:
            with csv_path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
                writer.writeheader()
                for r in kept_rows:
                    # Normalizar a las columnas esperadas (ignora extras).
                    writer.writerow({k: r.get(k, "") for k in _CSV_FIELDS})
            log.row("Filas transitorias purgadas (se reintentarán)", n_transient)
        except Exception as e:
            log.warning(f"No se pudo purgar filas transitorias del CSV: {e}.")

    return done


def _get_isins_from_db(conn, limit: Optional[int], seed: int) -> list:
    """
    Lista de ISINs KIID_Class=1 desde fund_kiid_metadata.
    Orden determinista por ISIN; muestreo reproducible si limit está dado.
    """
    rows = conn.execute(
        "SELECT ISIN FROM fund_kiid_metadata "
        "WHERE KIID_Class = 1 AND ISIN IS NOT NULL "
        "ORDER BY ISIN"
    ).fetchall()
    isins = [r[0] for r in rows]
    if limit is not None and limit < len(isins):
        rnd = random.Random(seed)
        isins = sorted(rnd.sample(isins, limit))
    return isins


def _csv_writer_append(csv_path: Path):
    """Abre el CSV en modo append, escribiendo cabecera si es nuevo."""
    is_new = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    f = csv_path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
    if is_new:
        writer.writeheader()
    return f, writer


# ──────────────────────────────────────────────────────────────────────────────
# Resumen agregado (se reconstruye SIEMPRE leyendo el CSV final completo,
# de modo que sea correcto aun tras varias pasadas reanudadas)
# ──────────────────────────────────────────────────────────────────────────────

def _emit_summary(csv_path: Path):
    if not csv_path.exists():
        log.warning("No hay CSV de resultados para resumir.")
        return

    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    n = len(rows)
    log.section("RESUMEN AGREGADO DE AUDITORÍA")
    log.row("Total ISINs auditados", n)
    if n == 0:
        return

    from collections import Counter
    verdicts  = Counter(r["verdict"] for r in rows)
    strategies = Counter(r["strategy"] for r in rows if r["strategy"])
    formats   = Counter(r["value_format"] for r in rows if r["value_format"])

    ok = sum(verdicts[v] for v in ("OK_PDFPLUMBER", "OK_HEURISTIC"))
    warn = sum(verdicts[v] for v in ("WARN_EUR_ONLY", "WARN_NO_OC"))
    fail = sum(verdicts[v] for v in ("FAIL_NONE", "FAIL_NO_URL", "FAIL_DOWNLOAD", "ERROR"))

    log.subsection("Tasa de éxito global")
    log.row("OK (tabla de costes útil)",      f"{ok:>5}  ({100*ok/n:.1f}%)")
    log.row("WARN (extrae, pero incompleto)", f"{warn:>5}  ({100*warn/n:.1f}%)")
    log.row("FAIL/ERROR (no usable)",         f"{fail:>5}  ({100*fail/n:.1f}%)")

    log.subsection("Distribución de veredictos")
    log.table(["verdict", "n", "pct_%"],
              [(v, c, f"{100*c/n:.1f}") for v, c in verdicts.most_common()])

    log.subsection("Distribución de estrategia")
    log.table(["strategy", "n", "pct_%"],
              [(s, c, f"{100*c/n:.1f}") for s, c in strategies.most_common()])

    log.subsection("Distribución de formato de valor")
    log.table(["value_format", "n", "pct_%"],
              [(s, c, f"{100*c/n:.1f}") for s, c in formats.most_common()])

    # ── Listas explícitas de ISINs problemáticos (para muestra dirigida) ──
    for tag in ("FAIL_NONE", "ERROR", "WARN_EUR_ONLY", "WARN_NO_OC"):
        bad = [r["ISIN"] for r in rows if r["verdict"] == tag]
        if bad:
            log.subsection(f"ISINs con veredicto {tag}  (n={len(bad)})")
            # En bloques de 6 por línea para legibilidad.
            for i in range(0, len(bad), 6):
                log._write("    " + "  ".join(bad[i:i + 6]))

    log.blank()
    log.warning("Candidatos a muestra dirigida: filtra el CSV por "
                "verdict IN (FAIL_NONE, ERROR, WARN_EUR_ONLY, WARN_NO_OC).")


# ──────────────────────────────────────────────────────────────────────────────
# Modos de ejecución
# ──────────────────────────────────────────────────────────────────────────────

def _run_from_pdf_dir(pdf_dir: Path, csv_path: Path, serialize_tables,
                      done: set):
    """Audita PDFs de una carpeta local. Nombre de fichero = ISIN.pdf."""
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    log.row("PDFs en carpeta", f"{len(pdfs)}  ({pdf_dir})")
    f, writer = _csv_writer_append(csv_path)
    try:
        for p in pdfs:
            isin = p.stem
            if isin in done:
                continue
            try:
                pdf_bytes = p.read_bytes()
            except Exception as e:
                row = {k: "" for k in _CSV_FIELDS}
                row.update({"ISIN": isin, "verdict": "FAIL_DOWNLOAD",
                            "failure_reason": f"read_error: {e}"})
                writer.writerow(row); f.flush()
                continue
            row = _audit_pdf_bytes(isin, str(p), pdf_bytes, serialize_tables)
            writer.writerow(row); f.flush()
            log.row(isin, f"{row['verdict']:<14} "
                          f"strat={row['strategy']} fmt={row['value_format']}")
    finally:
        f.close()


def _run_from_download(conn, isins: list, excel_master: str,
                       csv_path: Path, serialize_tables,
                       io_mod, done: set, throttle: float):
    """Audita descargando los PDFs reales vía io.download_pdf (sin caché)."""
    f, writer = _csv_writer_append(csv_path)
    session = io_mod._requests_session()
    n_total = len(isins)
    try:
        for idx, isin in enumerate(isins, 1):
            if isin in done:
                continue

            # 1. URLs desde el Excel maestro (reutiliza índice de io.py).
            try:
                urls = io_mod.find_kiid_links_from_excel(isin, excel_master)
            except Exception as e:
                urls = []
                log.warning(f"{isin}: error índice Excel: {e}")

            if not urls:
                row = {k: "" for k in _CSV_FIELDS}
                row.update({"ISIN": isin, "verdict": "FAIL_NO_URL",
                            "failure_reason": "sin_url_en_excel"})
                writer.writerow(row); f.flush()
                log.row(f"[{idx}/{n_total}] {isin}", "FAIL_NO_URL")
                continue

            # 2. Descargar bytes (primer URL que funcione).
            pdf_bytes = None
            last_err  = ""
            used_url  = ""
            for url in urls:
                try:
                    pdf_bytes = io_mod.download_pdf(url, session)
                    used_url = url
                    break
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    continue

            if not pdf_bytes:
                row = {k: "" for k in _CSV_FIELDS}
                row.update({"ISIN": isin, "kiid_url": urls[0],
                            "verdict": "FAIL_DOWNLOAD",
                            "failure_reason": last_err or "download_failed"})
                writer.writerow(row); f.flush()
                log.row(f"[{idx}/{n_total}] {isin}", f"FAIL_DOWNLOAD ({last_err})")
                if throttle:
                    time.sleep(throttle)
                continue

            # 3. Auditar.
            row = _audit_pdf_bytes(isin, used_url, pdf_bytes, serialize_tables)
            writer.writerow(row); f.flush()
            log.row(f"[{idx}/{n_total}] {isin}",
                    f"{row['verdict']:<14} strat={row['strategy']} "
                    f"fmt={row['value_format']}")

            if throttle:
                time.sleep(throttle)
    finally:
        f.close()


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main(
    limit:        Optional[int] = None,
    seed:         int           = 42,
    project_root: Optional[str] = None,
    from_pdf_dir: Optional[str] = None,
    csv_path:     Optional[str] = None,
    log_path:     Optional[str] = None,
    throttle:     float         = 0.0,
    restart:      bool          = False,
    db_path:      Optional[str] = None,
    master_excel: Optional[str] = None,
) -> None:

    root = _setup_path(project_root)

    # Cargar el serializador real (por ruta de fichero — no exige paquete).
    try:
        from core.dla_table_serializer import serialize_tables
    except Exception:
        import importlib.util as _ilu
        cand = root / "proyecto1" / "core" / "dla_table_serializer.py"
        if not cand.exists():
            print(f"[FATAL] No se encuentra dla_table_serializer.py en {cand}")
            sys.exit(2)
        spec = _ilu.spec_from_file_location("dla_table_serializer", cand)
        mod  = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        serialize_tables = mod.serialize_tables

    csv_file = Path(csv_path) if csv_path else root / "proyecto1" / "db" / "dla2_serializer_audit.csv"
    log_file = Path(log_path) if log_path else root / "proyecto1" / "db" / "dla2_serializer_audit.log"

    if restart and csv_file.exists():
        csv_file.unlink()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log._write("BL-DLA-2-AUDIT — Auditoría de éxito del serializador DLA-2")
    log._write(f"Ejecutado: {ts}")
    log._write(f"project_root: {root}")
    log._write(f"CSV:          {csv_file}")
    log._write(f"Log:          {log_file}")

    done = set() if restart else _load_done_isins(csv_file)
    if done:
        log.row("ISINs ya auditados (se saltan)", len(done))

    # ── Modo offline: carpeta de PDFs ──────────────────────────────────────
    if from_pdf_dir:
        log.section("MODO --from-pdf-dir (offline)")
        _run_from_pdf_dir(Path(from_pdf_dir), csv_file, serialize_tables, done)
        _emit_summary(csv_file)
        log.flush_to_file(log_file)
        return

    # ── Modo descarga: BD + Excel maestro ──────────────────────────────────
    log.section("MODO descarga (BD + Excel maestro)")
    import importlib
    try:
        io_mod = importlib.import_module("core.io")
    except ModuleNotFoundError:
        # Fallback por ruta de fichero si 'core' no resultó importable como
        # paquete (p. ej. ejecución desde una ubicación inesperada).
        import importlib.util as _ilu
        cand = root / "proyecto1" / "core" / "io.py"
        if not cand.exists():
            print(f"[FATAL] No se encuentra core/io.py en {cand}. "
                  f"Verifica --project-root.")
            sys.exit(2)
        spec = _ilu.spec_from_file_location("core.io", cand)
        io_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(io_mod)

    from shared.db import get_connection
    conn = get_connection(Path(db_path) if db_path else None)

    if master_excel:
        excel_master = master_excel
    else:
        from shared.config import MASTER_EXCEL
        excel_master = str(MASTER_EXCEL)
    log.row("Excel maestro", excel_master)

    isins = _get_isins_from_db(conn, limit=limit, seed=seed)
    log.row("ISINs a auditar (KIID_Class=1)", f"{len(isins)}"
            + (f"  [muestra limit={limit} seed={seed}]" if limit else "  [corpus completo]"))
    if throttle:
        log.row("Throttle entre descargas (s)", throttle)

    _run_from_download(conn, isins, excel_master, csv_file,
                       serialize_tables, io_mod, done, throttle)

    _emit_summary(csv_file)
    log.flush_to_file(log_file)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-AUDIT: auditoría de éxito real del serializador DLA-2.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--limit",        type=int,   default=None,
                        help="Audita solo N ISINs al azar (pasada de prueba).")
    parser.add_argument("--seed",         type=int,   default=42,
                        help="Semilla para el muestreo reproducible (con --limit).")
    parser.add_argument("--project-root", type=str,   default=None, dest="project_root")
    parser.add_argument("--from-pdf-dir", type=str,   default=None, dest="from_pdf_dir",
                        help="Audita PDFs de una carpeta local (nombre = ISIN.pdf).")
    parser.add_argument("--csv",          type=str,   default=None, dest="csv_path")
    parser.add_argument("--log",          type=str,   default=None, dest="log_path")
    parser.add_argument("--throttle",     type=float, default=0.0,
                        help="Pausa en segundos entre descargas (cortesía servidor).")
    parser.add_argument("--restart",      action="store_true",
                        help="Borra el CSV y empieza de cero (descarta lo auditado).")
    parser.add_argument("--db",           type=str,   default=None, dest="db_path",
                        help="Ruta alternativa a fondos.sqlite (override DB_PATH).")
    parser.add_argument("--master-excel", type=str,   default=None, dest="master_excel",
                        help="Ruta alternativa al Excel maestro (override MASTER_EXCEL).")
    args = parser.parse_args()

    main(
        limit=args.limit,
        seed=args.seed,
        project_root=args.project_root,
        from_pdf_dir=args.from_pdf_dir,
        csv_path=args.csv_path,
        log_path=args.log_path,
        throttle=args.throttle,
        restart=args.restart,
        db_path=args.db_path,
        master_excel=args.master_excel,
    )
