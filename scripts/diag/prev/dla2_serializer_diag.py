# -*- coding: utf-8 -*-
"""
scripts/diag/dla2_serializer_diag.py  v1.0  -- BL-DLA-2-DIAG-SERIALIZER
=============================================================
Diagnóstico de RECÁLCULO sobre la auditoría del serializador DLA-2.

PROPÓSITO:
    Consumir el CSV producido por dla2_serializer_audit.py (v1.2, con columnas
    val_entry/val_exit/val_oc/val_perf) y cruzarlo con el estado de coste REAL
    en fund_master, para responder, con datos del corpus completo:

      F1. ¿Cuál es la tasa de éxito real del serializador? (global y por subcausa)
      F2. ¿Cómo se distribuye cada veredicto del serializador frente a la
          Cost_Extraction_Quality actual (extraída del texto plano)?
      F3. BACKLOG DE PATRONES FALTANTES EN DLA-2  [núcleo de esta versión]
          Para cada fondo donde el serializador FALLA o extrae INCOMPLETO pero
          el texto plano SÍ capturó valor en BD: lista el ISIN, la subcausa, y
          el VALOR DE REFERENCIA que ya está en BD (el que DLA-2 debería haber
          capturado). Agrupado por subcausa para priorizar por volumen.
      F4. CASOS DE CONFLICTO
          Fondos donde DLA-2 extrajo un valor Y el texto plano tiene otro
          DISTINTO. El diagnóstico NO decide cuál es correcto — los expone para
          juicio humano (p.ej. Deutsche: texto plano Entry=0 vs tabla "500 EUR").

ENCUADRE CONCEPTUAL (corrección del usuario, sesión 2026-05-24):
    NO se concluye que "DLA-2 y texto plano sean complementarios". Lo que los
    datos muestran es que DLA-2 tiene PATRONES DE DETECCIÓN SIN IMPLEMENTAR que
    el camino de texto plano sí resuelve. DLA-2 es la fuente objetivo; los casos
    F3 son su lista de trabajo. El valor de referencia de BD se trata como
    CANDIDATO a verdad (verificable), no como verdad absoluta — de ahí F4.

NO HACE:
    No descarga PDFs, no ejecuta el serializador, no modifica la BD (solo lee
    fund_master). El recálculo es instantáneo y reejecutable: toda la parte
    cara (descarga + serialización) ya la hizo dla2_serializer_audit.py.

ENTRADAS:
    --audit-csv   CSV de dla2_serializer_audit.py (default: proyecto1/db/...).
    --db          fondos.sqlite (default: shared.config.DB_PATH).

SALIDAS:
    - Consola + log:  dla2_serializer_diag.log
    - CSV backlog F3: dla2_serializer_backlog.csv  (lista accionable de patrones)
    - CSV conflicto F4: dla2_serializer_conflicts.csv

USO:
    python -m scripts.diag.dla2_serializer_diag
    python -m scripts.diag.dla2_serializer_diag --audit-csv ruta.csv --db ruta.sqlite
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Resolución de raíz (misma heurística que el resto de scripts/diag)
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
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    p1 = root / "proyecto1"
    if p1.is_dir() and str(p1) not in sys.path:
        sys.path.insert(0, str(p1))
    return root


# ──────────────────────────────────────────────────────────────────────────────
# Logger dual (mismo patrón que dla2_decision_diag / dla2_serializer_audit)
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

    def row(self, label: str, value, width: int = 44):
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
# Normalización de valores: convierte el string crudo serializado o el valor de
# BD a un ratio decimal comparable (0.005 = 0,5%), o None si no es interpretable.
# ──────────────────────────────────────────────────────────────────────────────

# Base PRIIPs para convertir importes EUR a %: 500 EUR / 10000 = 0.05 = 5%.
_PRIIPS_BASE = 10000.0

_PAT_PCT_NUM = re.compile(r"(\d[\d.,]*)\s*%")
_PAT_EUR_NUM = re.compile(r"(\d[\d.,]*)\s*(?:EUR|USD|GBP|CHF|€|\$)", re.IGNORECASE)
# "ninguna", "ningún", "sin", "n/a", "no aplica" → coste cero declarado.
_PAT_ZERO_WORD = re.compile(r"\b(ningun[ao]?|sin\s+coste|n/?a|no\s+aplica)\b", re.IGNORECASE)


def _to_float_es(num_str: str) -> Optional[float]:
    """Convierte '1.234,56' o '0,74' o '500' a float. None si no parsea."""
    s = num_str.strip()
    if not s:
        return None
    # Formato español: '.' miles, ',' decimal. Si hay coma, es decimal.
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_value(raw, *, source: str) -> Optional[float]:
    """
    Devuelve un ratio decimal comparable a partir de un string crudo.

    source='serializer': el valor viene del serializador ("Hasta 500 EUR",
        "0,74 %", "0 EUR", "ninguna"). Los importes EUR se dividen por la base
        PRIIPs (500 EUR -> 0.05). Los % se pasan a ratio (0,74% -> 0.0074).
    source='db': el valor viene de fund_master (ya es un ratio decimal float,
        p.ej. 0.0074, o None). Se devuelve tal cual si es numérico.

    None si el valor es vacío o no interpretable.
    """
    if raw is None:
        return None

    if source == "db":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    # source == "serializer": parsear el string.
    s = str(raw).strip()
    if not s:
        return None

    if _PAT_ZERO_WORD.search(s):
        return 0.0

    m_pct = _PAT_PCT_NUM.search(s)
    if m_pct:
        v = _to_float_es(m_pct.group(1))
        return None if v is None else v / 100.0

    m_eur = _PAT_EUR_NUM.search(s)
    if m_eur:
        v = _to_float_es(m_eur.group(1))
        return None if v is None else v / _PRIIPS_BASE

    # Número pelado sin unidad (raro): tratarlo como % si es pequeño.
    v = _to_float_es(s)
    if v is not None:
        return v / 100.0 if v > 1.0 else v
    return None


# Tolerancia para considerar "igual" dos valores (5 basis points, coherente
# con COST_CROSS_VALIDATION_TOLERANCE_PCT del proyecto).
_EQ_TOLERANCE = 0.0005


def _values_match(a: Optional[float], b: Optional[float]) -> Optional[bool]:
    """True/False si ambos son numéricos; None si falta alguno (incomparable)."""
    if a is None or b is None:
        return None
    return abs(a - b) <= _EQ_TOLERANCE


# ──────────────────────────────────────────────────────────────────────────────
# Mapeo componente serializador <-> columna de BD
#   El componente OC del serializador ("Costes corrientes") es el que mapea a
#   Ongoing_Charge_Recurrent — el valor de coste más relevante y el que define
#   en gran medida la quality. Entry/Exit mapean a sus columnas homónimas.
# ──────────────────────────────────────────────────────────────────────────────

_COMPONENT_DB_MAP = {
    "oc":    ("val_oc",    "Ongoing_Charge_Recurrent"),
    "entry": ("val_entry", "Entry_Fee_Pct"),
    "exit":  ("val_exit",  "Exit_Fee_Pct"),
    "perf":  ("val_perf",  "Performance_Fee_Pct"),
}

# Veredictos del serializador considerados "no resueltos" (objeto del backlog).
_UNRESOLVED = {"FAIL_NONE", "WARN_NO_OC", "WARN_EUR_ONLY", "ERROR"}


# ──────────────────────────────────────────────────────────────────────────────
# Carga de datos
# ──────────────────────────────────────────────────────────────────────────────

def _load_audit_csv(path: Path) -> list:
    if not path.exists():
        log.warning(f"No se encuentra el CSV de auditoría: {path}")
        sys.exit(2)
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    # Validar que es la versión v1.2 (con columnas de valor).
    if rows and "val_oc" not in rows[0]:
        log.warning("El CSV de auditoría NO tiene columnas val_* (es < v1.2). "
                    "Re-ejecuta dla2_serializer_audit.py v1.2 para capturar valores. "
                    "F3/F4 no podrán comparar valores.")
    return rows


def _load_db_costs(conn, isins: list) -> dict:
    """
    Devuelve {ISIN: {Cost_Extraction_Quality, Ongoing_Charge_Recurrent,
    Entry_Fee_Pct, Exit_Fee_Pct, Performance_Fee_Pct}} para los ISINs dados.
    """
    out = {}
    cols = ("Cost_Extraction_Quality, Ongoing_Charge_Recurrent, "
            "Entry_Fee_Pct, Exit_Fee_Pct, Performance_Fee_Pct")
    # Consulta en bloques para no exceder límites de variables SQLite.
    isins = list(isins)
    CHUNK = 400
    for i in range(0, len(isins), CHUNK):
        chunk = isins[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        sql = (f"SELECT ISIN, {cols} FROM fund_master "
               f"WHERE ISIN IN ({placeholders})")
        for r in conn.execute(sql, chunk).fetchall():
            out[r[0]] = {
                "Cost_Extraction_Quality":  r[1],
                "Ongoing_Charge_Recurrent": r[2],
                "Entry_Fee_Pct":            r[3],
                "Exit_Fee_Pct":             r[4],
                "Performance_Fee_Pct":      r[5],
            }
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Fases del diagnóstico
# ──────────────────────────────────────────────────────────────────────────────

def fase1_tasa_exito(audit_rows: list):
    from collections import Counter
    log.section("F1 — Tasa de éxito del serializador (corpus auditado)")
    n = len(audit_rows)
    log.row("Total ISINs auditados", n)
    if n == 0:
        return

    verdicts = Counter(r["verdict"] for r in audit_rows)
    ok   = sum(verdicts[v] for v in ("OK_PDFPLUMBER", "OK_HEURISTIC"))
    warn = sum(verdicts[v] for v in ("WARN_EUR_ONLY", "WARN_NO_OC"))
    fail = sum(verdicts[v] for v in ("FAIL_NONE", "FAIL_NO_URL",
                                     "FAIL_DOWNLOAD", "ERROR"))
    log.subsection("Resumen")
    log.row("OK (tabla útil)",        f"{ok:>5}  ({100*ok/n:.1f}%)")
    log.row("WARN (incompleto)",      f"{warn:>5}  ({100*warn/n:.1f}%)")
    log.row("FAIL/ERROR (no usable)", f"{fail:>5}  ({100*fail/n:.1f}%)")

    log.subsection("Veredictos")
    log.table(["verdict", "n", "pct_%"],
              [(v, c, f"{100*c/n:.1f}") for v, c in verdicts.most_common()])

    # Subcausa de fallo (failure_reason) — separa los dos tipos de FAIL_NONE.
    reasons = Counter(r["failure_reason"] for r in audit_rows
                      if r["verdict"] in _UNRESOLVED and r.get("failure_reason"))
    if reasons:
        log.subsection("Subcausa de los no-resueltos (failure_reason)")
        log.table(["failure_reason", "n"],
                  [(k, c) for k, c in reasons.most_common()])


def fase2_matriz_quality(audit_rows: list, db_costs: dict):
    from collections import Counter, defaultdict
    log.section("F2 — Veredicto serializador x Cost_Extraction_Quality actual")
    cross = defaultdict(Counter)
    for r in audit_rows:
        q = db_costs.get(r["ISIN"], {}).get("Cost_Extraction_Quality", "(no en BD)")
        cross[r["verdict"]][q] += 1

    for v in ("OK_PDFPLUMBER", "OK_HEURISTIC", "WARN_EUR_ONLY",
              "WARN_NO_OC", "FAIL_NONE", "ERROR"):
        if v not in cross:
            continue
        log.subsection(v)
        log.table(["quality_actual", "n"],
                  [(q if q is not None else "NULL", c)
                   for q, c in cross[v].most_common()])

    log.blank()
    log.warning("Lectura: FAIL/WARN del serializador que YA son HIGH/MEDIUM en BD "
                "= casos que el texto plano resolvió y DLA-2 no -> patrón faltante "
                "(ver F3). NO implica complementariedad: es trabajo pendiente de DLA-2.")


def fase3_backlog_patrones(audit_rows: list, db_costs: dict,
                           backlog_csv: Path):
    """
    Núcleo. Para cada fondo no-resuelto por el serializador donde BD tiene
    valor de referencia, emite fila de backlog con el valor esperado.
    """
    from collections import Counter
    log.section("F3 — BACKLOG DE PATRONES FALTANTES EN DLA-2")

    fields = ["ISIN", "verdict", "failure_reason", "strategy",
              "component", "db_column",
              "serializer_raw", "serializer_norm",
              "db_reference_value", "comparable", "quality_actual"]
    out_rows = []
    by_subcause = Counter()

    for r in audit_rows:
        verdict = r["verdict"]
        if verdict not in _UNRESOLVED:
            continue
        db = db_costs.get(r["ISIN"], {})
        quality = db.get("Cost_Extraction_Quality")

        # Para cada componente, ¿el serializador falló en capturarlo pero BD
        # tiene un valor de referencia?
        for comp, (val_col, db_col) in _COMPONENT_DB_MAP.items():
            ser_raw  = (r.get(val_col) or "").strip()
            ser_norm = normalize_value(ser_raw, source="serializer") if ser_raw else None
            db_val   = normalize_value(db.get(db_col), source="db")

            # Caso de backlog: BD tiene valor pero el serializador NO lo capturó
            # (o lo capturó pero el veredicto global es no-resuelto, p.ej. EUR_ONLY).
            ser_missing = ser_raw == "" or ser_norm is None
            if db_val is not None and ser_missing:
                out_rows.append({
                    "ISIN": r["ISIN"], "verdict": verdict,
                    "failure_reason": r.get("failure_reason", ""),
                    "strategy": r.get("strategy", ""),
                    "component": comp, "db_column": db_col,
                    "serializer_raw": ser_raw,
                    "serializer_norm": "",
                    "db_reference_value": db_val,
                    "comparable": "REF_ONLY",
                    "quality_actual": quality,
                })
                by_subcause[(verdict, comp)] += 1

    # Escribir CSV de backlog.
    backlog_csv.parent.mkdir(parents=True, exist_ok=True)
    with backlog_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    log.row("Filas de backlog (componente recuperable con valor en BD)", len(out_rows))
    log.row("Fondos distintos afectados", len({r["ISIN"] for r in out_rows}))
    log.row("CSV backlog", backlog_csv)

    if by_subcause:
        log.subsection("Backlog por (veredicto, componente) — prioriza por volumen")
        log.table(["verdict", "component", "n_fondos"],
                  [(v, c, n) for (v, c), n in by_subcause.most_common()])

    # Top OC recuperables: el componente más relevante para la quality.
    oc_rows = [r for r in out_rows if r["component"] == "oc"]
    if oc_rows:
        log.subsection(f"Costes corrientes (OC) recuperables: {len(oc_rows)} fondos")
        log.warning("Estos fondos tienen OC en BD (texto plano) pero el "
                    "serializador no lo capturó. Son el objetivo prioritario: "
                    "OC define la quality. Valor esperado en columna "
                    "db_reference_value del CSV de backlog.")


def fase4_conflictos(audit_rows: list, db_costs: dict, conflict_csv: Path):
    """
    Fondos donde el serializador SÍ extrajo un valor y BD tiene OTRO distinto.
    No se decide cuál es correcto — se exponen para juicio humano.
    """
    log.section("F4 — CASOS DE CONFLICTO (serializador vs texto plano)")
    fields = ["ISIN", "verdict", "component", "db_column",
              "serializer_raw", "serializer_norm",
              "db_reference_value", "diff_bp", "quality_actual"]
    out_rows = []

    for r in audit_rows:
        db = db_costs.get(r["ISIN"], {})
        quality = db.get("Cost_Extraction_Quality")
        for comp, (val_col, db_col) in _COMPONENT_DB_MAP.items():
            ser_raw  = (r.get(val_col) or "").strip()
            if not ser_raw:
                continue
            ser_norm = normalize_value(ser_raw, source="serializer")
            db_val   = normalize_value(db.get(db_col), source="db")
            match = _values_match(ser_norm, db_val)
            if match is False:   # ambos numéricos y distintos -> conflicto
                out_rows.append({
                    "ISIN": r["ISIN"], "verdict": r["verdict"],
                    "component": comp, "db_column": db_col,
                    "serializer_raw": ser_raw,
                    "serializer_norm": round(ser_norm, 6),
                    "db_reference_value": round(db_val, 6),
                    "diff_bp": round(abs(ser_norm - db_val) * 10000, 1),
                    "quality_actual": quality,
                })

    conflict_csv.parent.mkdir(parents=True, exist_ok=True)
    with conflict_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in out_rows:
            w.writerow(row)

    log.row("Conflictos detectados (valor serializador != valor BD)", len(out_rows))
    log.row("Fondos distintos en conflicto", len({r["ISIN"] for r in out_rows}))
    log.row("CSV conflictos", conflict_csv)
    if out_rows:
        log.subsection("Muestra de conflictos (primeros 15, ordenados por diff)")
        sample = sorted(out_rows, key=lambda x: -x["diff_bp"])[:15]
        log.table(["ISIN", "comp", "serializer", "BD", "diff_bp", "quality"],
                  [(r["ISIN"], r["component"], r["serializer_raw"],
                    r["db_reference_value"], r["diff_bp"], r["quality_actual"])
                   for r in sample])
        log.blank()
        log.warning("El diagnóstico NO decide cuál es correcto. Recuerda el caso "
                    "Deutsche: texto plano Entry=0 era FALSO; la tabla tenía el "
                    "valor real. Requiere verificación sobre el PDF.")


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main(
    audit_csv:    Optional[str] = None,
    db_path:      Optional[str] = None,
    project_root: Optional[str] = None,
    log_path:     Optional[str] = None,
    backlog_csv:  Optional[str] = None,
    conflict_csv: Optional[str] = None,
) -> None:

    root = _setup_path(project_root)

    audit_file = Path(audit_csv) if audit_csv else root / "proyecto1" / "db" / "dla2_serializer_audit.csv"
    log_file   = Path(log_path) if log_path else root / "proyecto1" / "db" / "dla2_serializer_diag.log"
    backlog_f  = Path(backlog_csv) if backlog_csv else root / "proyecto1" / "db" / "dla2_serializer_backlog.csv"
    conflict_f = Path(conflict_csv) if conflict_csv else root / "proyecto1" / "db" / "dla2_serializer_conflicts.csv"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log._write("BL-DLA-2-DIAG-SERIALIZER — Recálculo sobre auditoría del serializador")
    log._write(f"Ejecutado: {ts}")
    log._write(f"project_root: {root}")
    log._write(f"Audit CSV:    {audit_file}")
    log._write(f"Log:          {log_file}")

    audit_rows = _load_audit_csv(audit_file)

    # Cargar costes de BD para los ISINs auditados.
    from shared.db import get_connection
    conn = get_connection(Path(db_path) if db_path else None)
    isins = [r["ISIN"] for r in audit_rows if r.get("ISIN")]
    db_costs = _load_db_costs(conn, isins)
    log.row("ISINs auditados", len(isins))
    log.row("ISINs encontrados en fund_master", len(db_costs))

    fase1_tasa_exito(audit_rows)
    fase2_matriz_quality(audit_rows, db_costs)
    fase3_backlog_patrones(audit_rows, db_costs, backlog_f)
    fase4_conflictos(audit_rows, db_costs, conflict_f)

    log.flush_to_file(log_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-DIAG-SERIALIZER: recálculo sobre la auditoría del serializador.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--audit-csv",    type=str, default=None, dest="audit_csv")
    parser.add_argument("--db",           type=str, default=None, dest="db_path")
    parser.add_argument("--project-root", type=str, default=None, dest="project_root")
    parser.add_argument("--log",          type=str, default=None, dest="log_path")
    parser.add_argument("--backlog-csv",  type=str, default=None, dest="backlog_csv")
    parser.add_argument("--conflict-csv", type=str, default=None, dest="conflict_csv")
    args = parser.parse_args()

    main(
        audit_csv=args.audit_csv,
        db_path=args.db_path,
        project_root=args.project_root,
        log_path=args.log_path,
        backlog_csv=args.backlog_csv,
        conflict_csv=args.conflict_csv,
    )
