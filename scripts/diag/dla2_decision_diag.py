# -*- coding: utf-8 -*-
"""
scripts/diag/dla2_decision_diag.py  v1.3  -- BL-DLA-2-DIAG / BL-COST-1
=============================================================
Orquestador único de toma de decisión para BL-DLA-2.

Integra secuencialmente todas las queries y análisis necesarios para
emitir la decisión Go/No-Go de BL-DLA-2, eliminando la necesidad de
ejecutar dla2_table_inventory.py, dla_diag_queries.py y
dla_check_exit_fee_cat.py por separado.

SECUENCIA DE EJECUCIÓN:
  Fase 1 — Cobertura actual de atributos objetivo      (Q-DLA2-01)
  Fase 2 — Patología de tabla serializada en texto     (Q-DLA2-02 / Q-DLA2-05a)
  Fase 3 — Inventario textual Cat. 1/2/3 por fondo     (Q-DLA2-03)
  Fase 4 — ROI real: cruce atributos NULL × categoría  (Q-DLA2-04a/b/c/d)
  Fase 5 — Fee_Known_Flag × tabla detectada            (Q-DLA2-05b)
  Fase 6 — Distribución cat_max para atributos NULL    (check_exit_fee_cat)
  Fase 7 — DECISIÓN GO/NO-GO (umbrales backlog v3.7)
  Fase 8 — Distribución de KID_Format inferred sobre corpus
  Fase 9 — Falsos positivos Entry_Fee_Pct=0
  Fase 10 — Falsos positivos Exit_Fee_Pct=0
  Fase 11 — Sospecha OC = ACI mal etiquetado

SALIDAS:
  - Consola: sección por sección con totales y decisión final
  - CSV:      <project_root>/proyecto1/db/dla2_table_inventory.csv
              (reutilizable por otros diagnósticos; se sobreescribe si existe)
  - Log:      <project_root>/proyecto1/db/dla2_decision_diag.log
              (copia íntegra de la salida de consola con timestamp)

PREREQUISITO TEMPORAL (documentado en backlog v3.7, BL-DLA-2-DIAG):
  Los resultados son fiables una vez Sub-fase 1D ha cubierto ≥30% del corpus
  (~1.000 fondos con DLA Fase 1 activo). El script advierte si no se cumple.

USO:
  python dla2_decision_diag.py
  python dla2_decision_diag.py --project-root C:\\desarrollo\\fondos
  python dla2_decision_diag.py --limit 300 --seed 42   # muestra aleatoria
  python dla2_decision_diag.py --skip-inventory        # reutilizar CSV existente
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import textwrap
from datetime import datetime
from io import StringIO
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# cost_format_signals: se importa por ruta de fichero (importlib.util) después
# de que _PROJECT_ROOT_AUTO esté resuelto. Esto evita depender de que proyecto1
# sea un paquete Python con __init__.py en la ruta de ejecución.
# La variable _CFS es None hasta que _load_cost_format_signals() se llama
# desde main() una vez que _setup_path() ha ampliado sys.path.
# ──────────────────────────────────────────────────────────────────────────────
# Resolución de raíz del proyecto
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


def _setup_path(explicit_root: str = None) -> Path:
    root = Path(explicit_root).resolve() if explicit_root else _PROJECT_ROOT_AUTO
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


# ──────────────────────────────────────────────────────────────────────────────
# Logger dual: consola + buffer (volcado a fichero al final)
# ──────────────────────────────────────────────────────────────────────────────

class _DualLogger:
    """Escribe en stdout y acumula en buffer para volcado a fichero."""

    def __init__(self):
        self._buf = StringIO()

    def _write(self, text: str):
        print(text)
        self._buf.write(text + "\n")

    def section(self, title: str):
        sep = "=" * 70
        self._write(f"\n{sep}")
        self._write(f"  {title}")
        self._write(sep)

    def subsection(self, title: str):
        self._write(f"\n  -- {title} --")

    def row(self, label: str, value, width: int = 38):
        self._write(f"  {label:<{width}}: {value}")

    def table(self, headers: list[str], rows: list[tuple], indent: int = 2):
        if not rows:
            self._write(" " * indent + "(sin resultados)")
            return
        widths = [len(h) for h in headers]
        for r in rows:
            for i, v in enumerate(r):
                widths[i] = max(widths[i], len(str(v) if v is not None else "NULL"))
        pad = " " * indent
        header_line = pad + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
        sep_line    = pad + "-" * (len(header_line) - indent)
        self._write(header_line)
        self._write(sep_line)
        for r in rows:
            self._write(pad + "  ".join(
                (str(v) if v is not None else "NULL").ljust(widths[i])
                for i, v in enumerate(r)
            ))

    def blank(self):
        self._write("")

    def warning(self, msg: str):
        self._write(f"  ⚠  {msg}")

    def decision(self, label: str, detail: str, remediation: str = ""):
        box = "=" * 70
        self._write(f"\n{box}")
        self._write(f"  >>> DECISIÓN: {label}")
        self._write(f"      {detail}")
        if remediation:
            self._write(f"      Acción: {remediation}")
        self._write(box)

    def flush_to_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._buf.getvalue(), encoding="utf-8")
        print(f"\n[LOG] Salida guardada en: {path}")


log = _DualLogger()

# ──────────────────────────────────────────────────────────────────────────────
# Loader de cost_format_signals por ruta de fichero
# ──────────────────────────────────────────────────────────────────────────────
_CFS = None   # módulo cargado por _load_cost_format_signals()

def _load_cost_format_signals(root: Path) -> bool:
    """
    Carga cost_format_signals.py por ruta de fichero.
    Retorna True si el módulo se cargó correctamente, False en caso contrario.
    Registra el módulo en sys.modules para evitar re-cargas.
    """
    global _CFS
    if _CFS is not None:
        return True
    candidates = [
        root / "proyecto1" / "scripts" / "diag" / "cost_format_signals.py",
        # Ruta alternativa si el script está en scripts/diag/
        Path(__file__).parent / "cost_format_signals.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            try:
                import importlib.util as _ilu
                spec = _ilu.spec_from_file_location("cost_format_signals", candidate)
                mod  = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                sys.modules["cost_format_signals"] = mod
                _CFS = mod
                print(f"[INFO] cost_format_signals cargado desde: {candidate}")
                return True
            except Exception as e:
                print(f"[WARN] No se pudo cargar cost_format_signals desde {candidate}: {e}")
    print("[WARN] cost_format_signals no encontrado — fases 8-11 producirán UNKNOWN/0.")
    print(f"  Rutas buscadas: {[str(c) for c in candidates]}")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Patrones de detección textual (extraídos de dla2_table_inventory.py)
# ──────────────────────────────────────────────────────────────────────────────

_PAT_COSTES_HEADER = re.compile(
    r'costes?\s+de\s+entrada'
    r'|costes?\s+de\s+salida'
    r'|costes?\s+corrientes?'
    r'|composici[oó]n\s+de\s+(los\s+)?costes?'
    r'|composition\s+of\s+(the\s+)?costs?'
    r'|ongoing\s+charges?'
    r'|entry\s+(charge|fee|cost)'
    r'|exit\s+(charge|fee|cost)'
    r'|total\s+expense\s+ratio'
    r'|\bTER\b'
    r'|costesdeentrada|costesdesalida|costescorrientes'
    r'|ongoingcharges?|costsofentry|costsofexiting',
    re.IGNORECASE,
)

_PAT_FEE_LINE = re.compile(
    r'(coste|comisi[oó]n|cargo|fee|charge|gasto|ongoing|entry|exit|'
    r'management|performance|rendimiento|[eé]xito)',
    re.IGNORECASE,
)
_PAT_PORCENTAJE = re.compile(
    r'\b\d{1,3}[,\.]\d{1,4}\s*%|\b0\s*%|\bnil\b|\bnone\b',
    re.IGNORECASE,
)
_PAT_POLITICA = re.compile(
    r'(acumulaci[oó]n|distribuci[oó]n|accumulation|distribution|'
    r'cobertura\s+de\s+divisa|currency\s+hedg|hedging\s+policy|'
    r'frecuencia\s+de\s+(pago|distribuci[oó]n)|distribution\s+frequency)',
    re.IGNORECASE,
)
_PAT_ESCENARIO = re.compile(
    r'escenario\s+(de\s+)?(tensi[oó]n|desfavorable|moderado|favorable)'
    r'|performance\s+scenario|stress\s+scenario'
    r'|rentabilidad\s+m[ií]nima\s+posible'
    r'|what\s+you\s+might\s+get\s+back',
    re.IGNORECASE,
)
_PAT_HORIZONTE    = re.compile(r'\b([1-9]|10)\s*(a[ñn]o|year)s?\b', re.IGNORECASE)
_PAT_LISTA_CLASES = re.compile(
    r'clases?\s+de\s+(participaci[oó]n|acciones?|units?)\s*(disponibles?|ofrecidas?)?'
    r'|share\s+class(es)?\s*(available|offered)?|clases?\s+disponibles?',
    re.IGNORECASE,
)
_PAT_LISTA_MONEDAS = re.compile(
    r'(monedas?\s+disponibles?|currencies?\s+available'
    r'|EUR|USD|GBP|CHF|JPY|NOK|SEK|CNH)\s*[,/]\s*(EUR|USD|GBP|CHF|JPY|NOK|SEK|CNH)',
    re.IGNORECASE,
)

# Q-DLA2-02: heurística léxica de patología cruce 2-col
_PAT_SOSPECHOSO = re.compile(
    r'\bTipo\s+(tres|cinco|diez)\s+a[ñn]os\b'
    r'|\bsubfondos\s+mide\b'
    r'|\bdurante\s+un\s+per[ií]odo\s+(El|La|Los|Las|Es)\s+'
    r'|\bproducto\s+(Este|Esta)\s+',
    re.IGNORECASE,
)

# Q-DLA2-05a: porcentaje en texto sin contexto fee
_PAT_PCT_FLOTANTE = re.compile(r'\b\d{1,2}[,\.]\d{2}\s*%')


def _count_fee_lines(text: str) -> int:
    lines = text.split('\n')
    count = sum(1 for l in lines if _PAT_FEE_LINE.search(l) and _PAT_PORCENTAJE.search(l))
    fused = text.replace(' ', '').lower()
    fused_hits = len(re.findall(
        r'(costesdeentrada|costesdesalida|costescorrientes|'
        r'ongoingcharges?|entrycost|exitcost)\d', fused,
    ))
    return count + fused_hits


def _analyze_text(isin, text, entry_null, exit_null, oc_null,
                  ap_null, hp_null, ch_null,
                  entry_fee_val=None, exit_fee_val=None, oc_val=None) -> dict:
    r = {
        "ISIN": isin,
        "text_len": 0,
        "has_cat1_signal": 0, "has_cat2_costes": 0,
        "has_cat2_politica": 0, "has_cat3_escenarios": 0,
        "n_pct_values": 0, "n_fee_lines": 0,
        "entry_fee_null": int(entry_null), "exit_fee_null": int(exit_null),
        "oc_null": int(oc_null), "acc_policy_null": int(ap_null),
        "hedging_null": int(hp_null), "ch_null": int(ch_null),
        "cat_max": 0, "processing_error": "",
        "is_sospechoso": 0, "has_pct_flotante": 0,
        # v1.3 BL-COST-1: campos de coste (inicializados a valores vacíos)
        "kid_format_inferred": "UNKNOWN",
        "priips_signals_count": 0,
        "ucits_signals_count": 0,
        "eur_near_costs_count": 0,
        "entry_fee_suspect": 0,
        "entry_fee_suspect_signals": 0,
        "exit_fee_suspect": 0,
        "exit_fee_suspect_signals": 0,
        "oc_aci_gap_suspect": 0,
    }
    if not text or not text.strip():
        r["processing_error"] = "empty_text"
        return r
    try:
        r["text_len"]       = len(text)
        r["n_pct_values"]   = len(_PAT_PORCENTAJE.findall(text))
        r["n_fee_lines"]    = _count_fee_lines(text)
        r["has_cat2_costes"]   = int(bool(_PAT_COSTES_HEADER.search(text)) or r["n_fee_lines"] >= 2)
        r["has_cat2_politica"] = int(bool(_PAT_POLITICA.search(text)))
        r["has_cat1_signal"]   = int(bool(_PAT_LISTA_CLASES.search(text)) or
                                      bool(_PAT_LISTA_MONEDAS.search(text)))
        if _PAT_ESCENARIO.search(text):
            horizontes = set(_PAT_HORIZONTE.findall(text))
            r["has_cat3_escenarios"] = int(len(horizontes) >= 2)
        r["is_sospechoso"]   = int(bool(_PAT_SOSPECHOSO.search(text)))
        r["has_pct_flotante"] = int(bool(_PAT_PCT_FLOTANTE.search(text)))

        if   r["has_cat3_escenarios"]:                              r["cat_max"] = 3
        elif r["has_cat2_costes"] or r["has_cat2_politica"]:       r["cat_max"] = 2
        elif r["has_cat1_signal"]:                                  r["cat_max"] = 1

        # v1.3 BL-COST-1: señales de formato KID y patologías de coste.
        # _CFS es el módulo cost_format_signals cargado por _load_cost_format_signals().
        # Si _CFS es None (no se pudo cargar), los campos permanecen en su valor
        # por defecto (UNKNOWN/0) — no bloquea el análisis principal.
        if _CFS is not None:
            try:
                _sigs = _CFS.count_kid_format_signals(text)
                r["kid_format_inferred"]   = _CFS.detect_kid_format(text)
                r["priips_signals_count"]  = _sigs["priips_count"]
                r["ucits_signals_count"]   = _sigs["ucits_count"]
                r["eur_near_costs_count"]  = _sigs["eur_near_costs_count"]

                if entry_fee_val is not None:
                    _efp = _CFS.detect_entry_fee_false_positive(text, float(entry_fee_val))
                    r["entry_fee_suspect"]         = int(_efp["is_suspect"])
                    r["entry_fee_suspect_signals"] = _efp["signal_count"]

                if exit_fee_val is not None:
                    _xfp = _CFS.detect_exit_fee_false_positive(text, float(exit_fee_val))
                    r["exit_fee_suspect"]         = int(_xfp["is_suspect"])
                    r["exit_fee_suspect_signals"] = _xfp["signal_count"]

                if oc_val is not None:
                    _ocg = _CFS.detect_oc_aci_gap(text, float(oc_val))
                    r["oc_aci_gap_suspect"] = int(_ocg["is_suspect"])
            except Exception as _cost_exc:
                # Error en la detección de un fondo concreto — no bloquear
                r["kid_format_inferred"] = "UNKNOWN"

    except Exception as exc:
        r["processing_error"] = str(exc)[:200]
    return r


# ──────────────────────────────────────────────────────────────────────────────
# DDL tabla temporal SQLite
# ──────────────────────────────────────────────────────────────────────────────

_DDL_INV = """
CREATE TEMP TABLE dla_inv (
    ISIN TEXT, text_len INTEGER,
    has_cat1_signal INTEGER, has_cat2_costes INTEGER,
    has_cat2_politica INTEGER, has_cat3_escenarios INTEGER,
    n_pct_values INTEGER, n_fee_lines INTEGER,
    entry_fee_null INTEGER, exit_fee_null INTEGER,
    oc_null INTEGER, acc_policy_null INTEGER,
    hedging_null INTEGER, ch_null INTEGER,
    cat_max INTEGER, processing_error TEXT,
    is_sospechoso INTEGER, has_pct_flotante INTEGER,
    -- v1.3 BL-COST-1: 9 columnas nuevas de diagnóstico de coste
    kid_format_inferred TEXT,
    priips_signals_count INTEGER,
    ucits_signals_count INTEGER,
    eur_near_costs_count INTEGER,
    entry_fee_suspect INTEGER,
    entry_fee_suspect_signals INTEGER,
    exit_fee_suspect INTEGER,
    exit_fee_suspect_signals INTEGER,
    oc_aci_gap_suspect INTEGER
)
"""

_CSV_FIELDS = [
    "ISIN", "text_len",
    "has_cat1_signal", "has_cat2_costes", "has_cat2_politica",
    "has_cat3_escenarios", "n_pct_values", "n_fee_lines",
    "entry_fee_null", "exit_fee_null", "oc_null",
    "acc_policy_null", "hedging_null", "ch_null",
    "cat_max", "processing_error",
    "is_sospechoso", "has_pct_flotante",
    # v1.3 BL-COST-1: 9 columnas nuevas de diagnóstico de coste
    "kid_format_inferred",
    "priips_signals_count",
    "ucits_signals_count",
    "eur_near_costs_count",
    "entry_fee_suspect",
    "entry_fee_suspect_signals",
    "exit_fee_suspect",
    "exit_fee_suspect_signals",
    "oc_aci_gap_suspect",
]


# ──────────────────────────────────────────────────────────────────────────────
# FASE 1: Cobertura actual de atributos objetivo (Q-DLA2-01)
# ──────────────────────────────────────────────────────────────────────────────

def fase1_cobertura_atributos(conn) -> dict:
    log.section("FASE 1 — Cobertura actual de atributos objetivo  (Q-DLA2-01)")

    row = conn.execute("""
        SELECT
            SUM(CASE WHEN Entry_Fee_Pct       IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Exit_Fee_Pct        IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Ongoing_Charge      IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Accumulation_Policy IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Distribution_Frequency IS NULL
                     AND Accumulation_Policy = 'DISTRIBUTION' THEN 1 ELSE 0 END),
            SUM(CASE WHEN Hedging_Policy      IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Currency_Hedged     IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN Fund_Currency       IS NULL THEN 1 ELSE 0 END),
            SUM(CASE WHEN (
                    Entry_Fee_Pct IS NULL OR Exit_Fee_Pct IS NULL
                    OR Ongoing_Charge IS NULL OR Accumulation_Policy IS NULL
                    OR Hedging_Policy IS NULL OR Currency_Hedged IS NULL
                ) THEN 1 ELSE 0 END),
            COUNT(*)
        FROM fund_master
    """).fetchone()

    (null_entry, null_exit, null_oc, null_acc, null_dist,
     null_hedging, null_ch, null_fcur, null_any, total) = row

    log.subsection("Atributos de costes (Cat. 2 — impacto BL-51A / BL-55)")
    log.row("Entry_Fee_Pct NULL",     f"{null_entry:>5}  ({null_entry/total*100:.1f}%)")
    log.row("Exit_Fee_Pct NULL",      f"{null_exit:>5}  ({null_exit/total*100:.1f}%)")
    log.row("Ongoing_Charge NULL",    f"{null_oc:>5}  ({null_oc/total*100:.1f}%)")

    log.subsection("Atributos de política (Cat. 2 — impacto BL-49)")
    log.row("Accumulation_Policy NULL",            f"{null_acc:>5}  ({null_acc/total*100:.1f}%)")
    log.row("Distribution_Frequency NULL (DIST.)", f"{null_dist:>5}  ({null_dist/total*100:.1f}%)")
    log.row("Hedging_Policy NULL",                 f"{null_hedging:>5}  ({null_hedging/total*100:.1f}%)")
    log.row("Currency_Hedged NULL",                f"{null_ch:>5}  ({null_ch/total*100:.1f}%)")

    log.subsection("Atributos de lista (Cat. 1)")
    log.row("Fund_Currency NULL",     f"{null_fcur:>5}  ({null_fcur/total*100:.1f}%)")

    log.blank()
    log.row("FONDOS CON ≥1 ATRIBUTO NULL",  f"{null_any:>5}  ({null_any/total*100:.1f}%)")
    log.row("Total fondos",                 f"{total:>5}")

    return {
        "null_entry": null_entry, "null_exit": null_exit,
        "null_oc": null_oc, "null_acc": null_acc,
        "null_hedging": null_hedging, "null_ch": null_ch,
        "null_any": null_any, "total": total,
    }


# ──────────────────────────────────────────────────────────────────────────────
# FASE 2: Patología de cruce 2-col en texto (Q-DLA2-02 / Q-DLA2-05a)
# ──────────────────────────────────────────────────────────────────────────────

def fase2_patologia_texto(conn) -> dict:
    log.section("FASE 2 — Patología de tabla serializada en texto  (Q-DLA2-02 / Q-DLA2-05a)")

    # Registrar función REGEXP en SQLite
    def _regexp(pattern, value):
        if value is None:
            return 0
        return 1 if re.search(pattern, value, re.IGNORECASE) else 0

    conn.create_function("REGEXP", 2, _regexp)

    # Q-DLA2-02: heurística léxica (firmas de cruce 2-col)
    row_sospechosos = conn.execute(r"""
        SELECT COUNT(*) AS n_sospechosos
        FROM fund_kiid_metadata
        WHERE Raw_KIID_Text REGEXP '\bTipo\s+(tres|cinco|diez)\s+a[ñn]os\b'
           OR Raw_KIID_Text REGEXP '\bsubfondos\s+mide\b'
           OR Raw_KIID_Text REGEXP '\bdurante\s+un\s+per[ií]odo\s+(El|La|Los|Las|Es)\s+'
           OR Raw_KIID_Text REGEXP '\bproducto\s+(Este|Esta)\s+'
    """).fetchone()
    n_sospechosos = row_sospechosos[0]

    total_kiid = conn.execute(
        "SELECT COUNT(*) FROM fund_kiid_metadata WHERE Raw_KIID_Text IS NOT NULL"
    ).fetchone()[0]

    log.subsection("Q-DLA2-02: fondos con firmas léxicas de cruce 2-col (texto pre-DLA)")
    log.row("Fondos sospechosos", f"{n_sospechosos:>5}  ({n_sospechosos/max(total_kiid,1)*100:.1f}%)")
    log.row("Total con Raw_KIID_Text", f"{total_kiid:>5}")
    if n_sospechosos == 0:
        log.warning("0 sospechosos: texto ya procesado con DLA-1 o corpus sin patología 2-col residual.")

    # Q-DLA2-05a: % flotante en NOT_FOUND sin contexto fee
    row_05a = conn.execute(r"""
        SELECT COUNT(*) AS n_patologia
        FROM fund_kiid_metadata km
        JOIN fund_master fm ON fm.ISIN = km.ISIN
        WHERE fm.Entry_Fee_Pct IS NULL
          AND fm.Fee_Known_Flag = 'NOT_FOUND'
          AND km.Raw_KIID_Text REGEXP '\b\d{1,2}[,\.]\d{2}\s*%'
    """).fetchone()
    n_05a = row_05a[0]

    # Total NOT_FOUND con Entry_Fee NULL para calcular porcentaje
    n_not_found = conn.execute("""
        SELECT COUNT(*) FROM fund_master
        WHERE Entry_Fee_Pct IS NULL AND Fee_Known_Flag = 'NOT_FOUND'
    """).fetchone()[0]

    log.subsection("Q-DLA2-05a: Entry_Fee=NULL + Fee_Known_Flag=NOT_FOUND con % en texto")
    log.row("Con % flotante en texto",  f"{n_05a:>5}  ({n_05a/max(n_not_found,1)*100:.1f}% de NOT_FOUND)")
    log.row("Total NOT_FOUND sin entry", f"{n_not_found:>5}")
    log.blank()
    log.row("Interpretación",
            "Fondos donde existe un % en el KIID pero el detector no lo extrajo.")
    log.row("",
            "Son candidatos directos a mejora por BL-DLA-2 (estructura de tabla).")

    return {"n_sospechosos": n_sospechosos, "n_05a": n_05a, "n_not_found": n_not_found}


# ──────────────────────────────────────────────────────────────────────────────
# FASE 3: Inventario textual Cat. 1/2/3 (Q-DLA2-03)
# ──────────────────────────────────────────────────────────────────────────────

def fase3_inventario(conn, limit: int, seed: int, csv_path: Path,
                     skip_inventory: bool) -> tuple[int, dict]:
    """
    Analiza Raw_KIID_Text de cada fondo y clasifica en Cat. 0/1/2/3.
    Si skip_inventory=True y el CSV existe, lo carga sin re-analizar.
    Devuelve (n_procesados, counters).
    """
    import random

    log.section("FASE 3 — Inventario textual Cat. 1/2/3  (Q-DLA2-03)")

    # ── Cargar o generar inventario ──────────────────────────────────────────

    if skip_inventory and csv_path.exists():
        log.row("Modo",      "Reutilizando CSV existente (--skip-inventory)")
        log.row("CSV",       str(csv_path))
        rows_db = []          # no se necesita; cargamos directo
    else:
        # v1.3 BL-COST-1: añadir Entry_Fee_Pct, Exit_Fee_Pct, Ongoing_Charge(_Recurrent)
        # para detectar falsos positivos y gap OC-ACI.
        # La columna puede llamarse Ongoing_Charge (v18) u Ongoing_Charge_Recurrent (v19).
        _oc_col = "Ongoing_Charge_Recurrent"
        _oc_check = conn.execute(
            "SELECT name FROM pragma_table_info('fund_master') WHERE name=?",
            ("Ongoing_Charge_Recurrent",)
        ).fetchone()
        if not _oc_check:
            _oc_col = "Ongoing_Charge"

        rows_db = conn.execute(f"""
            SELECT
                km.ISIN,
                km.Raw_KIID_Text,
                CASE WHEN fm.Entry_Fee_Pct       IS NULL THEN 1 ELSE 0 END,
                CASE WHEN fm.Exit_Fee_Pct        IS NULL THEN 1 ELSE 0 END,
                CASE WHEN fm.{_oc_col}           IS NULL THEN 1 ELSE 0 END,
                CASE WHEN fm.Accumulation_Policy IS NULL THEN 1 ELSE 0 END,
                CASE WHEN fm.Hedging_Policy      IS NULL THEN 1 ELSE 0 END,
                CASE WHEN fm.Currency_Hedged     IS NULL THEN 1 ELSE 0 END,
                fm.Entry_Fee_Pct,
                fm.Exit_Fee_Pct,
                fm.{_oc_col}
            FROM fund_kiid_metadata km
            JOIN fund_master fm ON fm.ISIN = km.ISIN
            WHERE km.KIID_Class  = 1
              AND km.KIID_Status IN ('OK', 'CACHED')
              AND km.Raw_KIID_Text IS NOT NULL
              AND LENGTH(km.Raw_KIID_Text) > 100
            ORDER BY km.ISIN
        """).fetchall()

        if not rows_db:
            log.warning("ERROR: no se encontraron fondos con Raw_KIID_Text. Abortando.")
            sys.exit(1)

        universo = len(rows_db)
        if limit and limit < universo:
            random.seed(seed)
            rows_db = random.sample(rows_db, limit)
            log.row("Muestra aleatoria", f"{len(rows_db)} fondos (seed={seed}, universo={universo})")
        else:
            log.row("Universo completo", f"{universo} fondos")

        # Analizar y escribir CSV
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        n_cat  = [0, 0, 0, 0]
        n_err  = 0
        total  = len(rows_db)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
            writer.writeheader()
            for idx, db_row in enumerate(rows_db):
                (isin, text,
                 en, ex, oc, ap, hp, ch,
                 entry_fee_val, exit_fee_val, oc_val) = db_row
                result = _analyze_text(isin, text, en, ex, oc, ap, hp, ch,
                                       entry_fee_val=entry_fee_val,
                                       exit_fee_val=exit_fee_val,
                                       oc_val=oc_val)
                writer.writerow({k: result[k] for k in _CSV_FIELDS})
                if result["processing_error"]:
                    n_err += 1
                else:
                    n_cat[result["cat_max"]] += 1
                if (idx + 1) % 200 == 0 or (idx + 1) == total:
                    pct = (idx + 1) / total * 100
                    print(f"  {idx+1:>5}/{total}  ({pct:4.1f}%)  "
                          f"cat0={n_cat[0]}  cat1={n_cat[1]}  "
                          f"cat2={n_cat[2]}  cat3={n_cat[3]}  err={n_err}")

        log.blank()
        log.row("CSV generado", str(csv_path))

    # ── Cargar CSV en tabla temporal ─────────────────────────────────────────

    conn.execute(_DDL_INV)
    _BOOL_FIELDS = {
        "has_cat1_signal", "has_cat2_costes", "has_cat2_politica",
        "has_cat3_escenarios", "entry_fee_null", "exit_fee_null",
        "oc_null", "acc_policy_null", "hedging_null", "ch_null",
        "is_sospechoso", "has_pct_flotante",
    }
    insert_sql = f"INSERT INTO dla_inv VALUES ({','.join(['?']*len(_CSV_FIELDS))})"
    n_loaded = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            def _cv(k, v):
                if k in _BOOL_FIELDS:
                    return 1 if v in ("True", "1", "true") else 0
                return v
            conn.execute(insert_sql, [_cv(k, row.get(k, "")) for k in _CSV_FIELDS])
            n_loaded += 1
    conn.commit()
    log.row("Filas cargadas en dla_inv", n_loaded)

    # ── Resumen Cat. 0/1/2/3 ────────────────────────────────────────────────

    dist_rows = conn.execute("""
        SELECT cat_max, COUNT(*) AS n,
               ROUND(COUNT(*)*100.0/SUM(COUNT(*)) OVER (), 1) AS pct
        FROM dla_inv WHERE processing_error=''
        GROUP BY cat_max ORDER BY cat_max
    """).fetchall()
    n_err_loaded = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE processing_error!=''"
    ).fetchone()[0]
    n_ok = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE processing_error=''"
    ).fetchone()[0]

    log.blank()
    log.subsection("Distribución de señal textual por categoría")
    log.table(["cat_max", "n_fondos", "pct_%"], dist_rows)
    log.blank()
    log.row("Fondos sin error", n_ok)
    log.row("Fondos con error", n_err_loaded)

    # Extraer conteos por categoría para uso posterior
    cat_counts = {int(r[0]): int(r[1]) for r in dist_rows}
    return n_ok, cat_counts


# ──────────────────────────────────────────────────────────────────────────────
# FASE 4: ROI real cruce NULL × categoría (Q-DLA2-04a/b/c/d)
# ──────────────────────────────────────────────────────────────────────────────

def fase4_roi(conn) -> dict:
    log.section("FASE 4 — ROI real: cruce atributos NULL × categoría  (Q-DLA2-04)")

    # Q-DLA2-04a: ROI global
    log.subsection("Q-DLA2-04a: ROI global")
    row_a = conn.execute("""
        SELECT
            SUM(CASE WHEN has_cat2_costes=1
                     AND (entry_fee_null=1 OR exit_fee_null=1 OR oc_null=1)
                THEN 1 ELSE 0 END)              AS roi_costes_accionable,
            SUM(CASE WHEN has_cat2_costes=1
                THEN 1 ELSE 0 END)              AS total_con_cat2_costes,
            SUM(CASE WHEN has_cat2_politica=1
                     AND (acc_policy_null=1 OR hedging_null=1 OR ch_null=1)
                THEN 1 ELSE 0 END)              AS roi_politica_accionable,
            SUM(CASE WHEN has_cat3_escenarios=1
                THEN 1 ELSE 0 END)              AS scope_dla3,
            SUM(CASE WHEN cat_max=0
                THEN 1 ELSE 0 END)              AS sin_senal,
            COUNT(*)                             AS total_analizados
        FROM dla_inv WHERE processing_error=''
    """).fetchone()
    (roi_costes, total_cat2, roi_politica, scope3, sin_senal, total) = row_a

    log.table(
        ["métrica", "valor"],
        [
            ("roi_costes_accionable  (Cat2 costes ∩ NULL coste)",  roi_costes),
            ("total_con_cat2_costes  (señal Cat2 costes presente)", total_cat2),
            ("roi_politica_accionable (Cat2 política ∩ NULL política)", roi_politica),
            ("scope_dla3             (Cat3 escenarios detectados)", scope3),
            ("sin_senal              (Cat0, sin ninguna señal)",    sin_senal),
            ("total_analizados",                                    total),
        ]
    )

    # Q-DLA2-04b: desglose por atributo de coste
    log.subsection("Q-DLA2-04b: ROI desglosado por atributo de coste")
    row_b = conn.execute("""
        SELECT
            SUM(CASE WHEN has_cat2_costes=1 AND entry_fee_null=1 THEN 1 ELSE 0 END),
            SUM(CASE WHEN has_cat2_costes=1 AND exit_fee_null=1  THEN 1 ELSE 0 END),
            SUM(CASE WHEN has_cat2_costes=1 AND oc_null=1        THEN 1 ELSE 0 END)
        FROM dla_inv WHERE processing_error=''
    """).fetchone()
    log.table(
        ["atributo", "fondos_accionables"],
        [
            ("Entry_Fee_Pct", row_b[0]),
            ("Exit_Fee_Pct",  row_b[1]),
            ("Ongoing_Charge",row_b[2]),
        ]
    )

    # Q-DLA2-04c: distribución completa de categorías
    log.subsection("Q-DLA2-04c: distribución de categorías (confirmación)")
    rows_c = conn.execute("""
        SELECT cat_max, COUNT(*) AS n,
               ROUND(COUNT(*)*100.0/SUM(COUNT(*)) OVER (), 1) AS pct
        FROM dla_inv WHERE processing_error=''
        GROUP BY cat_max ORDER BY cat_max
    """).fetchall()
    log.table(["cat_max", "n_fondos", "pct_%"], rows_c)

    # Q-DLA2-04d: errores
    log.subsection("Q-DLA2-04d: errores de procesamiento")
    rows_d = conn.execute("""
        SELECT processing_error, COUNT(*) AS n
        FROM dla_inv WHERE processing_error!=''
        GROUP BY processing_error ORDER BY n DESC
    """).fetchall()
    if not rows_d:
        log.row("Errores", "0  ✓")
    else:
        log.table(["processing_error", "n"], rows_d)

    return {
        "roi_costes": roi_costes, "total_cat2": total_cat2,
        "roi_politica": roi_politica, "scope3": scope3,
        "sin_senal": sin_senal, "total": total,
        "entry_accionable": row_b[0],
        "exit_accionable":  row_b[1],
        "oc_accionable":    row_b[2],
    }


# ──────────────────────────────────────────────────────────────────────────────
# FASE 5: Fee_Known_Flag × tabla detectada (Q-DLA2-05b)
# ──────────────────────────────────────────────────────────────────────────────

def fase5_fee_flag(conn) -> None:
    log.section("FASE 5 — Fee_Known_Flag × tabla detectada  (Q-DLA2-05b)")

    rows = conn.execute("""
        SELECT
            fm.Fee_Known_Flag,
            COUNT(*)                                               AS n_fondos,
            SUM(CASE WHEN inv.has_cat2_costes=1 THEN 1 ELSE 0 END) AS con_cat2_costes,
            SUM(CASE WHEN fm.Entry_Fee_Pct IS NULL THEN 1 ELSE 0 END) AS null_entry_fee
        FROM fund_master fm
        LEFT JOIN dla_inv inv ON inv.ISIN = fm.ISIN
        GROUP BY fm.Fee_Known_Flag
        ORDER BY n_fondos DESC
    """).fetchall()

    log.table(
        ["Fee_Known_Flag", "n_fondos", "con_cat2_costes", "null_entry_fee"],
        rows
    )
    log.blank()
    log.row("Interpretación",
            "NOT_FOUND con cat2_costes presente → candidatos directos a BL-DLA-2.")


# ──────────────────────────────────────────────────────────────────────────────
# FASE 6: Distribución por categoría REAL para cada atributo NULL
#         BL-DLA-2-LOGIC-FIX v1.1: usa categoría específica por atributo,
#         no cat_max global (un fondo con Cat.3 TAMBIÉN tiene Cat.2).
# ──────────────────────────────────────────────────────────────────────────────

# Mapeo explícito atributo → flag de categoría real en dla_inv
# Regla: Entry_Fee_Pct / Exit_Fee_Pct / Ongoing_Charge están SIEMPRE en
#        tabla Cat. 2 (Costes), nunca en Cat. 3 (Escenarios PRIIPS).
#        Accumulation_Policy / Hedging_Policy / Currency_Hedged están en
#        tabla Cat. 2 (Política), nunca en Cat. 3.
ATTRIBUTE_CATEGORY_MAPPING = {
    "Entry_Fee_Pct":        ("has_cat2_costes",    "Cat. 2 (Costes)"),
    "Exit_Fee_Pct":         ("has_cat2_costes",    "Cat. 2 (Costes)"),
    "Ongoing_Charge":       ("has_cat2_costes",    "Cat. 2 (Costes)"),
    "Accumulation_Policy":  ("has_cat2_politica",  "Cat. 2 (Política)"),
    "Hedging_Policy":       ("has_cat2_politica",  "Cat. 2 (Política)"),
    "Currency_Hedged":      ("has_cat2_politica",  "Cat. 2 (Política)"),
}


def fase6_atributo_vs_cat(conn) -> dict:
    """
    Para cada atributo NULL, muestra cuántos fondos SÍ tienen la categoría
    donde ese atributo reside (accionables) vs cuántos no la tienen (legítima
    ausencia de tabla o sólo Cat. 3).

    Corrección BL-DLA-2-LOGIC-FIX: en v1.0 se agrupaba por cat_max, lo que
    clasificaba erróneamente los fondos con {Cat.2 + Cat.3} (cat_max=3) como
    si sus comisiones de salida estuvieran en Cat. 3. Cat. 2 y Cat. 3 NO son
    mutuamente excluyentes; un fondo puede tener ambas tablas.
    """
    log.section("FASE 6 — Categoría real por atributo NULL  [v1.1 — BL-DLA-2-LOGIC-FIX]")

    atributos = [
        ("Exit_Fee_Pct",        "exit_fee_null=1"),
        ("Entry_Fee_Pct",       "entry_fee_null=1"),
        ("Ongoing_Charge",      "oc_null=1"),
        ("Accumulation_Policy", "acc_policy_null=1"),
        ("Hedging_Policy",      "hedging_null=1"),
        ("Currency_Hedged",     "ch_null=1"),
    ]

    resumen = {}
    for label, cond in atributos:
        total_null = conn.execute(
            f"SELECT COUNT(*) FROM dla_inv WHERE {cond} AND processing_error=''"
        ).fetchone()[0]
        if total_null == 0:
            continue

        cat_flag, cat_label = ATTRIBUTE_CATEGORY_MAPPING[label]

        # Contar fondos NULL que SÍ tienen la categoría correcta (accionables)
        # y los que NO la tienen (no accionables por BL-DLA-2)
        row_cat = conn.execute(f"""
            SELECT
                SUM(CASE WHEN {cat_flag}=1 THEN 1 ELSE 0 END)  AS con_cat_real,
                SUM(CASE WHEN {cat_flag}=0 THEN 1 ELSE 0 END)  AS sin_cat_real,
                SUM(CASE WHEN has_cat3_escenarios=1 AND {cat_flag}=0
                    THEN 1 ELSE 0 END)                          AS solo_cat3
            FROM dla_inv
            WHERE {cond} AND processing_error=''
        """).fetchone()
        con_cat, sin_cat, solo_cat3 = row_cat

        log.subsection(f"{label} NULL  (total: {total_null})")
        log.table(
            ["estado", "n", "pct_%", "interpretación"],
            [
                (f"Con {cat_label}",
                 con_cat,
                 f"{con_cat/total_null*100:.1f}%",
                 "ACCIONABLE por BL-DLA-2"),
                (f"Sin {cat_label} (sólo Cat.1/0)",
                 sin_cat - solo_cat3,
                 f"{(sin_cat - solo_cat3)/total_null*100:.1f}%",
                 "No accionable (sin tabla Cat.2)"),
                ("Sólo Cat. 3 (sin Cat.2)",
                 solo_cat3,
                 f"{solo_cat3/total_null*100:.1f}%",
                 "Scope BL-DLA-3, no BL-DLA-2"),
            ]
        )

        resumen[label] = {
            "total_null":  total_null,
            "accionable":  con_cat,
            "solo_cat3":   solo_cat3,
            "sin_cat":     sin_cat - solo_cat3,
            "pct_accionable": con_cat / total_null * 100 if total_null else 0.0,
        }

    # Nota explicativa sobre el fix
    log.blank()
    log.warning(
        "v1.0 agrupaba por cat_max: fondos con {Cat.2+Cat.3} (cat_max=3) "
        "aparecían como 'Cat.3', sobreestimando NULLs en Cat.3 para comisiones. "
        "v1.1 consulta la flag de categoría real de cada atributo — corrección BL-DLA-2-LOGIC-FIX."
    )

    return resumen


# ──────────────────────────────────────────────────────────────────────────────
# FASE 7: DECISIÓN GO/NO-GO (umbrales backlog v3.7)
# ──────────────────────────────────────────────────────────────────────────────

def fase7_decision(conn, f1: dict, f4: dict, f6: dict, n_ok: int,
                   cat_counts: dict) -> None:
    log.section("FASE 7 — DECISIÓN GO/NO-GO  (umbrales backlog v3.7 / v1.1 BL-DLA-2-LOGIC-FIX)")

    total    = f4["total"]
    universo = f1["total"]

    # ── Prevalencia Cat. 2 REAL (BL-DLA-2-LOGIC-FIX) ────────────────────────
    # v1.0 usaba cat_max==2, que excluía fondos con {Cat.2+Cat.3} (cat_max=3).
    # v1.1 usa has_cat2_costes OR has_cat2_politica — cualquier fondo con tabla
    # Cat.2 presente, independientemente de si también tiene Cat.3.
    n_cat2_real = conn.execute("""
        SELECT COUNT(*) FROM dla_inv
        WHERE (has_cat2_costes=1 OR has_cat2_politica=1)
          AND processing_error=''
    """).fetchone()[0]
    pct_cat2 = n_cat2_real / max(n_ok, 1) * 100

    # cat_max==2 (legacy, para comparación)
    n_cat2_puro = cat_counts.get(2, 0)

    # ROI de Cat. 2 sobre atributos de coste
    roi_costes = f4["roi_costes"]

    # Prevalencia Cat. 3
    n_cat3   = cat_counts.get(3, 0)
    pct_cat3 = n_cat3 / max(n_ok, 1) * 100

    # ── Indicadores clave ────────────────────────────────────────────────────
    log.subsection("Indicadores clave para la decisión  [v1.1 — BL-DLA-2-LOGIC-FIX]")
    log.table(
        ["indicador", "valor", "umbral_backlog"],
        [
            ("Cat. 2 prevalencia REAL (has_cat2_*=1)",
             f"{n_cat2_real} ({pct_cat2:.1f}%)",
             "≥40% → GO  |  20-40% → PILOTO  |  <20% → DIFERIR"),
            ("Cat. 2 pura (cat_max=2, legacy v1.0)",
             f"{n_cat2_puro} ({n_cat2_puro/max(n_ok,1)*100:.1f}%)",
             "Referencia histórica — subestima Cat.2"),
            ("roi_costes_accionable",
             f"{roi_costes}",
             "—  (complementario, no determinante)"),
            ("Cat. 3 prevalencia (cat_max=3)",
             f"{n_cat3} ({pct_cat3:.1f}%)",
             "Scope BL-DLA-3"),
            ("Fondos con ≥1 atributo NULL",
             f"{f1['null_any']} ({f1['null_any']/universo*100:.1f}%)",
             "Beneficio potencial total"),
        ]
    )

    # ── Resumen accionabilidad Exit_Fee_Pct (atributo crítico) ───────────────
    log.subsection("Exit_Fee_Pct NULL — accionabilidad real por BL-DLA-2")
    exit_info = f6.get("Exit_Fee_Pct", {})
    if exit_info:
        total_exit_null = exit_info["total_null"]
        accionable  = exit_info["accionable"]
        solo_cat3   = exit_info["solo_cat3"]
        sin_cat     = exit_info["sin_cat"]
        log.row("  Accionables (tiene Cat.2 Costes)",
                f"{accionable}  ({accionable/total_exit_null*100:.1f}%)")
        log.row("  Sólo Cat. 3 (no accionable BL-DLA-2)",
                f"{solo_cat3}  ({solo_cat3/total_exit_null*100:.1f}%)")
        log.row("  Sin Cat.2 ni Cat.3",
                f"{sin_cat}  ({sin_cat/total_exit_null*100:.1f}%)")
        log.blank()
        if solo_cat3 > accionable:
            log.warning(
                f"La mayoría de los Exit_Fee NULL ({solo_cat3/total_exit_null*100:.0f}%) "
                f"están sólo en Cat.3 → BL-DLA-2 (Cat.1+2) no los resolverá. "
                f"Scope real de BL-DLA-3."
            )

    # ── Decisión principal ───────────────────────────────────────────────────
    log.blank()

    if pct_cat2 >= 40:
        decision   = "GO COMPLETO"
        detalle    = "Cat. 2 ≥40% del corpus. ROI alto. Implementar BL-DLA-2 Cat.1+2 completo."
        accion     = "Iniciar Sub-fase 2A (dla_table_serializer.py v1.0)."
        dla3_nota  = ""
    elif pct_cat2 >= 20:
        decision   = "GO ACOTADO — PILOTO PREVIO"
        detalle    = "Cat. 2 entre 20-40%. Proceder con piloto de 50 ISINs con Cat.2 detectada."
        accion     = "Seleccionar 50 ISINs con cat_max=2 del CSV y ejecutar Sub-fase 2A piloto."
        dla3_nota  = ""
    else:
        decision   = "DIFERIR BL-DLA-2"
        detalle    = (
            f"Cat. 2 real solo en {pct_cat2:.1f}% del corpus (<20%). "
            f"Los atributos NULL residen principalmente en Cat.3 ({pct_cat3:.1f}%)."
        )
        accion = (
            "Priorizar BL-DLA-3-DIAG (Q-DLA-06 sobre el CSV ya generado). "
            "Revisar BL-55 y BL-51A residual para cobertura de Exit_Fee sin tablas."
        )
        dla3_nota = (
            f"BL-DLA-3-DIAG puede ejecutarse inmediatamente: "
            f"el CSV dla2_table_inventory.csv contiene has_cat3_escenarios. "
            f"Q-DLA-06 = filtrar CSV donde cat_max=3 y cruzar con atributos NULL."
        )

    log.decision(decision, detalle, accion)

    if dla3_nota:
        log.blank()
        log.subsection("Implicación para BL-DLA-3-DIAG")
        log.row("Nota", textwrap.fill(dla3_nota, width=70))

    # ── Nota de fiabilidad ───────────────────────────────────────────────────
    log.blank()
    log.warning(
        "Estos resultados son mayoritariamente sobre texto pre-DLA-1. "
        "Repetir el diagnóstico tras ≥300 fondos procesados con DLA-1 activo "
        "para obtener la cifra definitiva (backlog v3.8, BL-DLA-2-DIAG)."
    )


# ──────────────────────────────────────────────────────────────────────────────
# FASE 8: Distribución de KID_Format inferred sobre corpus (BL-COST-1)
# ──────────────────────────────────────────────────────────────────────────────

def fase8_kid_format_distribution(conn, n_ok: int) -> dict:
    """Distribución de KID_Format inferido por cost_format_signals.detect_kid_format."""
    log.section("FASE 8 — Distribución KID_Format inferred  [BL-COST-1 v1.3]")

    # La columna kid_format_inferred puede ser NULL si cost_format_signals no se importó
    rows = conn.execute("""
        SELECT
            COALESCE(kid_format_inferred, 'UNKNOWN') AS fmt,
            COUNT(*) AS n,
            ROUND(COUNT(*) * 100.0 / ?, 1) AS pct
        FROM dla_inv
        WHERE processing_error = ''
        GROUP BY fmt
        ORDER BY n DESC
    """, (max(n_ok, 1),)).fetchall()

    log.table(["kid_format", "n_fondos", "pct_%"], rows)
    log.blank()

    counts = {r[0]: r[1] for r in rows}
    n_priips  = counts.get("PRIIPS_KID",  0)
    n_ucits   = counts.get("UCITS_KIID",  0)
    n_unknown = counts.get("UNKNOWN",     0)

    if n_ok > 0:
        pct_priips = n_priips / n_ok * 100
        if pct_priips >= 80:
            log.warning(
                f"BL-COST-1-FORMAT: {pct_priips:.1f}% PRIIPS_KID — "
                f"router obligatorio en Sprint 2 (CRITERIO C ≥80%)."
            )
        elif pct_priips >= 30:
            log.warning(
                f"BL-COST-1-FORMAT: {pct_priips:.1f}% PRIIPS_KID — "
                f"router recomendado en Sprint 2."
            )
        else:
            log.row("CRITERIO C", f"PRIIPS_KID {pct_priips:.1f}% (<30%) — reconsiderar alcance Sprint 2")

    log.blank()
    log.row("Señales PRIIPs (mediana fondos PRIIPS)",
            conn.execute(
                "SELECT ROUND(AVG(priips_signals_count),1) FROM dla_inv "
                "WHERE kid_format_inferred='PRIIPS_KID' AND processing_error=''"
            ).fetchone()[0] or "N/A")
    log.row("Valores EUR cerca costes (avg PRIIPS)",
            conn.execute(
                "SELECT ROUND(AVG(eur_near_costs_count),1) FROM dla_inv "
                "WHERE kid_format_inferred='PRIIPS_KID' AND processing_error=''"
            ).fetchone()[0] or "N/A")

    return {"n_priips": n_priips, "n_ucits": n_ucits, "n_unknown": n_unknown}


# ──────────────────────────────────────────────────────────────────────────────
# FASE 9: Falsos positivos Entry_Fee_Pct=0 (BL-COST-1)
# ──────────────────────────────────────────────────────────────────────────────

def fase9_entry_fee_false_positives(conn) -> int:
    """Fondos con Entry_Fee_Pct=0 en BD pero evidencia textual de fee no-cero."""
    log.section("FASE 9 — Falsos positivos Entry_Fee_Pct=0  [BL-COST-1 v1.3]")

    n_entry_zero = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Entry_Fee_Pct = 0.0"
    ).fetchone()[0]
    n_suspect = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE entry_fee_suspect=1 AND processing_error=''"
    ).fetchone()[0]

    log.row("Fondos con Entry_Fee_Pct=0 en BD", n_entry_zero)
    log.row("Sospechosos (evidencia textual fee≠0)",
            f"{n_suspect}  ({n_suspect/max(n_entry_zero,1)*100:.1f}% de los zeros)")

    if n_suspect > 0:
        top_suspect = conn.execute("""
            SELECT di.ISIN, di.entry_fee_suspect_signals, fm.Fee_Known_Flag
            FROM dla_inv di
            JOIN fund_master fm ON fm.ISIN = di.ISIN
            WHERE di.entry_fee_suspect=1 AND di.processing_error=''
            ORDER BY di.entry_fee_suspect_signals DESC
            LIMIT 10
        """).fetchall()
        log.blank()
        log.subsection("Top ISINs sospechosos (Entry)")
        log.table(["ISIN", "n_señales", "Fee_Known_Flag"], top_suspect)

    corpus_size = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE processing_error=''"
    ).fetchone()[0]
    pct_corpus = n_suspect / max(corpus_size, 1) * 100
    log.blank()
    log.row("% sobre corpus analizado", f"{pct_corpus:.2f}%")
    if pct_corpus >= 3:
        log.warning(
            f"[BL-COST-1-FP-ENTRY] {pct_corpus:.1f}% ≥ 3% umbral BLOQUEANTE "
            f"Sprint 2 (CRITERIO B). Router obligatorio."
        )
    elif pct_corpus >= 1:
        log.warning(
            f"[BL-COST-1-FP-ENTRY] {pct_corpus:.1f}% (1-3%) — diferible "
            f"pero revisar los casos paradigmáticos."
        )
    else:
        log.row("CRITERIO B (Entry)", f"{pct_corpus:.2f}% <1% — diferible")

    return n_suspect


# ──────────────────────────────────────────────────────────────────────────────
# FASE 10: Falsos positivos Exit_Fee_Pct=0 (BL-COST-1)
# ──────────────────────────────────────────────────────────────────────────────

def fase10_exit_fee_false_positives(conn) -> int:
    """Fondos con Exit_Fee_Pct=0 en BD pero evidencia textual de fee no-cero."""
    log.section("FASE 10 — Falsos positivos Exit_Fee_Pct=0  [BL-COST-1 v1.3]")

    n_exit_zero = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct = 0.0"
    ).fetchone()[0]
    n_suspect = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE exit_fee_suspect=1 AND processing_error=''"
    ).fetchone()[0]

    log.row("Fondos con Exit_Fee_Pct=0 en BD", n_exit_zero)
    log.row("Sospechosos (evidencia textual fee≠0)",
            f"{n_suspect}  ({n_suspect/max(n_exit_zero,1)*100:.1f}% de los zeros)")

    if n_suspect > 0:
        top_suspect = conn.execute("""
            SELECT di.ISIN, di.exit_fee_suspect_signals, fm.Fee_Known_Flag
            FROM dla_inv di
            JOIN fund_master fm ON fm.ISIN = di.ISIN
            WHERE di.exit_fee_suspect=1 AND di.processing_error=''
            ORDER BY di.exit_fee_suspect_signals DESC
            LIMIT 10
        """).fetchall()
        log.blank()
        log.subsection("Top ISINs sospechosos (Exit)")
        log.table(["ISIN", "n_señales", "Fee_Known_Flag"], top_suspect)

    corpus_size = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE processing_error=''"
    ).fetchone()[0]
    pct_corpus = n_suspect / max(corpus_size, 1) * 100
    log.blank()
    log.row("% sobre corpus analizado", f"{pct_corpus:.2f}%")
    if pct_corpus >= 3:
        log.warning(
            f"[BL-COST-1-FP-EXIT] {pct_corpus:.1f}% ≥ 3% umbral BLOQUEANTE Sprint 2."
        )

    return n_suspect


# ──────────────────────────────────────────────────────────────────────────────
# FASE 11: Sospecha OC = ACI mal etiquetado (BL-COST-1)
# ──────────────────────────────────────────────────────────────────────────────

def fase11_oc_aci_gap(conn) -> dict:
    """Fondos donde Ongoing_Charge en BD coincide con ACI en texto (mal etiquetado)."""
    log.section("FASE 11 — Sospecha OC = ACI mal etiquetado  [BL-COST-1 v1.3]")

    n_oc_not_null = conn.execute(
        "SELECT COUNT(*) FROM dla_inv di "
        "JOIN fund_master fm ON fm.ISIN = di.ISIN "
        "WHERE (fm.Ongoing_Charge IS NOT NULL OR "
        "       (SELECT name FROM pragma_table_info('fund_master') "
        "        WHERE name='Ongoing_Charge_Recurrent') IS NOT NULL) "
        "AND di.processing_error = ''"
    ).fetchone()[0]
    # Contar directamente desde la columna dla_inv
    n_suspect = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE oc_aci_gap_suspect=1 AND processing_error=''"
    ).fetchone()[0]

    log.row("Fondos con OC/OC_Recurrent no NULL procesados", n_oc_not_null)
    log.row("Sospechosos (OC coincide con ACI en texto)",
            f"{n_suspect}")

    if n_suspect > 0:
        top = conn.execute("""
            SELECT di.ISIN, fm.Ongoing_Charge
            FROM dla_inv di
            JOIN fund_master fm ON fm.ISIN = di.ISIN
            WHERE di.oc_aci_gap_suspect = 1 AND di.processing_error = ''
            ORDER BY fm.Ongoing_Charge DESC NULLS LAST
            LIMIT 10
        """).fetchall()
        log.blank()
        log.subsection("Top ISINs con sospecha OC=ACI (Ongoing_Charge col v18)")
        log.table(["ISIN", "OC_en_BD"], top)

    log.blank()
    log.row("Interpretación",
            "Estos fondos tienen un OC que coincide numericamente con el ACI@RHP.")
    log.row("",
            "El OC podría ser ACI mal etiquetado (incluye amortizacion one-offs).")
    log.row("",
            "Sprint 2 desambiguara con priips_cost_extractor.py.")

    return {"n_suspect": n_suspect}


# ──────────────────────────────────────────────────────────────────────────────
# RESUMEN BL-COST-1 (Norma 7.5 del spec)
# ──────────────────────────────────────────────────────────────────────────────

def _emit_blcost1_summary(conn, n_ok: int, f11: dict) -> None:
    """Emite el bloque de resumen BL-COST-1 según Norma 7.5."""
    n_priips = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE kid_format_inferred='PRIIPS_KID' "
        "AND processing_error=''"
    ).fetchone()[0]
    n_ucits = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE kid_format_inferred='UCITS_KIID' "
        "AND processing_error=''"
    ).fetchone()[0]
    n_unknown = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE "
        "(kid_format_inferred='UNKNOWN' OR kid_format_inferred IS NULL) "
        "AND processing_error=''"
    ).fetchone()[0]
    n_fp_entry = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE entry_fee_suspect=1 AND processing_error=''"
    ).fetchone()[0]
    n_fp_exit = conn.execute(
        "SELECT COUNT(*) FROM dla_inv WHERE exit_fee_suspect=1 AND processing_error=''"
    ).fetchone()[0]
    n_oc_gap = f11.get("n_suspect", 0)

    total = max(n_ok, 1)
    log.section("RESUMEN BL-COST-1 DIAGNOSTIC v1.3")
    log._write(f"--- RESUMEN BL-COST-1 DIAGNOSTIC v1.3 ---")
    log._write(f"[INFO] BL-COST-1-FORMAT PRIIPS_KID: {n_priips} fondos ({n_priips/total*100:.1f}%)")
    log._write(f"[INFO] BL-COST-1-FORMAT UCITS_KIID:  {n_ucits} fondos ({n_ucits/total*100:.1f}%)")
    log._write(f"[INFO] BL-COST-1-FORMAT UNKNOWN:     {n_unknown} fondos ({n_unknown/total*100:.1f}%)")
    log._write(f"[WARN] BL-COST-1-FP-ENTRY: {n_fp_entry} fondos sospechosos ({n_fp_entry/total*100:.2f}%)")
    log._write(f"[WARN] BL-COST-1-FP-EXIT:  {n_fp_exit} fondos sospechosos ({n_fp_exit/total*100:.2f}%)")
    log._write(f"[WARN] BL-COST-1-OC-GAP:   {n_oc_gap} fondos sospechosos")
    log._write(f"---")
    log.blank()

    # Criterios Go/No-Go Sprint 2
    pct_priips = n_priips / total * 100
    pct_fp_entry = n_fp_entry / total * 100
    criterio_a_met = True  # Cat2 >= 40% — ya evaluado en fase 7
    criterio_b_bloqueante = pct_fp_entry >= 3.0
    criterio_c_obligatorio = pct_priips >= 80.0

    log.subsection("CRITERIOS GO/NO-GO SPRINT 2 (actualización post-v1.3)")
    log.table(
        ["criterio", "valor", "resultado"],
        [
            ("A: Cat2 prevalencia ≥40%", "ver Fase 7", "evaluado en Fase 7"),
            (f"B: FP entry ≥3% corpus", f"{pct_fp_entry:.2f}%",
             "BLOQUEANTE — router obligatorio" if criterio_b_bloqueante else "diferible"),
            (f"C: PRIIPS_KID ≥80%", f"{pct_priips:.1f}%",
             "router OBLIGATORIO" if criterio_c_obligatorio else
             ("router recomendado" if pct_priips >= 30 else "reconsiderar alcance")),
        ]
    )


# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint principal
# ──────────────────────────────────────────────────────────────────────────────

def main(
    limit:          int  = None,
    seed:           int  = 42,
    project_root:   str  = None,
    skip_inventory: bool = False,
    csv_path:       str  = None,
    log_path:       str  = None,
) -> None:

    root = _setup_path(project_root)
    _load_cost_format_signals(root)  # carga _CFS para fases 8-11

    csv_file = Path(csv_path) if csv_path else root / "proyecto1" / "db" / "dla2_table_inventory.csv"
    log_file = Path(log_path) if log_path else root / "proyecto1" / "db" / "dla2_decision_diag.log"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log._write(f"BL-DLA-2-DIAG — Orquestador de toma de decisión")
    log._write(f"Ejecutado: {ts}")
    log._write(f"project_root: {root}")
    log._write(f"CSV:          {csv_file}")
    log._write(f"Log:          {log_file}")

    from shared.db import get_connection
    conn = get_connection()

    # Ejecutar fases secuencialmente
    f1 = fase1_cobertura_atributos(conn)

    f2 = fase2_patologia_texto(conn)

    n_ok, cat_counts = fase3_inventario(
        conn, limit=limit, seed=seed,
        csv_path=csv_file, skip_inventory=skip_inventory,
    )

    f4 = fase4_roi(conn)

    fase5_fee_flag(conn)

    f6 = fase6_atributo_vs_cat(conn)

    fase7_decision(conn, f1=f1, f4=f4, f6=f6, n_ok=n_ok, cat_counts=cat_counts)

    # v1.3 BL-COST-1: fases nuevas de diagnóstico de coste
    fase8_kid_format_distribution(conn, n_ok)
    fase9_entry_fee_false_positives(conn)
    fase10_exit_fee_false_positives(conn)
    f11 = fase11_oc_aci_gap(conn)

    _emit_blcost1_summary(conn, n_ok, f11)

    # Volcar log a fichero
    log.flush_to_file(log_file)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BL-DLA-2-DIAG: orquestador único de toma de decisión.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Ejemplos:
              python dla2_decision_diag.py
              python dla2_decision_diag.py --limit 300 --seed 42
              python dla2_decision_diag.py --skip-inventory
              python dla2_decision_diag.py --project-root C:\\desarrollo\\fondos
        """),
    )
    parser.add_argument("--project-root",    type=str,  default=None, dest="project_root")
    parser.add_argument("--limit",           type=int,  default=None)
    parser.add_argument("--seed",            type=int,  default=42)
    parser.add_argument("--skip-inventory",  action="store_true", dest="skip_inventory",
                        help="Reutilizar CSV existente sin re-analizar el corpus.")
    parser.add_argument("--csv",             type=str,  default=None,
                        help="Ruta alternativa del CSV de inventario.")
    parser.add_argument("--log",             type=str,  default=None,
                        help="Ruta alternativa del fichero de log.")
    args = parser.parse_args()

    main(
        limit=args.limit,
        seed=args.seed,
        project_root=args.project_root,
        skip_inventory=args.skip_inventory,
        csv_path=args.csv,
        log_path=args.log,
    )
