# -*- coding: utf-8 -*-
"""
core/pipeline.py

Pipeline canónico de Proyecto 1:
- carga maestro
- IO documental KIID
- parsing genérico (texto + visual)
- clasificación por bloque
- persistencia en SQLite
"""

import datetime
import time
import gc
from pathlib import Path
from typing import Optional, List, Dict, Any

import pandas as pd

from core.io import get_kiid_for_isin
from core.kiid_parser import parse_kiid_generic
from core.classify_utils import (
    detect_strategy        as _detect_strategy,
    detect_benchmark_type  as _detect_benchmark_type,
)
try:
    from proyecto1.core.fund_characterizer import characterize_fund, is_structured_product
except ImportError:
    from core.fund_characterizer import characterize_fund, is_structured_product
from core.sqlite_writer import publish_fund, log_ingestion

#print("[DEBUG] Cargo pipeline.py")        

# -------------------------------------------------
# Carga de maestro (USADO POR run_block.py)
# -------------------------------------------------

def load_master_excel(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)

    isin_candidates = {"isin", "codigo isin", "código isin", "isin code"}
    name_candidates = {"nombre", "nombre de fondo", "nombrefondo", "fund_name"}

    frames = []

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)

        # normalizar columnas
        norm_cols = {str(c).strip().lower(): c for c in df.columns}

        isin_col = next((norm_cols[c] for c in isin_candidates if c in norm_cols), None)
        name_col = next((norm_cols[c] for c in name_candidates if c in norm_cols), None)

        if not isin_col or not name_col:
            continue

        df = df.rename(columns={
            isin_col: "ISIN",
            name_col: "Fund_Name",
        })

        df = df[df["ISIN"].notna()].copy()

        # la gestora viene del nombre de la hoja
        df["Management_Company"] = sheet.strip()

        frames.append(df[["ISIN", "Fund_Name", "Management_Company"]])

    if not frames:
        raise ValueError("No se ha encontrado ninguna hoja válida con columna ISIN.")

    master_df = pd.concat(frames, ignore_index=True)
    return master_df


def load_master_excelPrevio(path: Path) -> pd.DataFrame:
    xls = pd.ExcelFile(path)
    isin_candidates = {"isin", "codigo isin", "código isin", "isin code"}
    name_candidates = {"nombre", "nombre de fondo", "nombrefondo", "fund_name"}
    management_candidates = {"gestora", "gestora del fondo", "Management_Company"}

    for sheet in xls.sheet_names:
        df = xls.parse(sheet)
        for c in df.columns:
            print("[DEBUG] columnas df " + c)        
        norm_cols = {str(c).strip().lower(): c for c in df.columns}

        isin_col = next((norm_cols[c] for c in isin_candidates if c in norm_cols), None)
        name_col = next((norm_cols[c] for c in name_candidates if c in norm_cols), None)
        mgmt_col = next((norm_cols[c] for c in management_candidates if c in norm_cols), None)
        
        if isin_col and name_col:
            col_map = {isin_col: "ISIN", name_col: "Fund_Name"}
            if mgmt_col:
                col_map[mgmt_col] = "Management_Company"
            
            
            df = df.rename(columns=col_map)
            df = df[df["ISIN"].notna()]
            return df

    raise ValueError("No se ha encontrado columna ISIN en el maestro.")



# -------------------------------------------------
# Utilidades
# -------------------------------------------------

def dynamic_getattr(mod, names):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None


# -------------------------------------------------
# Data Quality derivation (canónica)
# -------------------------------------------------
def _derive_data_quality_flag(parsed: dict) -> str:
    srri_q = parsed.get("SRRI_Quality_Flag")

    if srri_q in (None, "NONE"):
        return "MISSING"

    if srri_q == "LOW_CONFLICT":
        return "WARN"

    return "OK"



def _get_available_isins(conn, df_master: pd.DataFrame) -> list[str]:
    """
    Devuelve los ISIN del maestro que aún no han sido clasificados
    en ningún bloque previo (Heuristic_Block IS NULL).
    """
    all_isins = set(df_master["ISIN"].dropna().unique())

    rows = conn.execute(
        """
        SELECT DISTINCT ISIN
        FROM fund_master
        WHERE Heuristic_Block IS NOT NULL
        """
    ).fetchall()

    classified_isins = {r[0] for r in rows if r[0]}

    return sorted(all_isins - classified_isins)



def validate_classification_contract(
    classification: Dict[str, Any],
    block_name: str,
    isin: str,
) -> None:
    CANONICAL_KEYS = {
        # Canonico original
        "Fund_Nature",
        "Profile",
        "Type",
        "Family",
        "Style_Profile",
        "Geography",
        "Theme",
        "Exposure_Bias",
        "Subtype",
        # Canonico v2
        "Strategy",
        "Is_ESG",
        "Benchmark_Type",
        # Canonico v3 — fund_characterizer nuevos atributos
        "Market_Cap_Focus",
        "Sector_Focus",
        "Currency_Hedged",
        "Investment_Universe",
        "Accumulation_Policy",
    }

    # --- claves inesperadas ---
    extra_keys = set(classification) - CANONICAL_KEYS
    if extra_keys:
        raise ValueError(
            f"[{block_name}] ISIN {isin} - claves no canónicas: {extra_keys}"
        )

    # --- clave obligatoria ---
    if classification.get("Fund_Nature") is None:
        raise ValueError(
            f"[{block_name}] ISIN {isin} - Fund_Nature es obligatoria"
        )

    # --- tipos ---
    # Is_ESG es int (0/1), el resto son str o None
    _INT_KEYS = {"Is_ESG"}
    for k, v in classification.items():
        if v is None:
            continue
        if k in _INT_KEYS:
            if not isinstance(v, int):
                raise ValueError(
                    f"[{block_name}] ISIN {isin} - {k} debe ser int, got {type(v)}"
                )
        elif not isinstance(v, str):
            raise ValueError(
                f"[{block_name}] ISIN {isin} - {k} debe ser str o None (recibido {type(v)})"
            )





# -------------------------------------------------
# Ejecución de bloque
# -------------------------------------------------

def run_block(
    block_module,
    df_master: pd.DataFrame,
    conn,
    master_excel_path: Path,
    sample_size: Optional[int] = None,
    stop_on_error: bool = False,
    #list_isin: Optional[List[str]] = None,
    list_isin: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:

    block_name = getattr(block_module, "BLOCK_NAME", block_module.__name__).upper()
    heuristic_core = 0 if block_name == "RESTANTES" else 1

    get_universe = dynamic_getattr(
        block_module,
        ["get_universe_isins", "get_heuristic_isins", "get_universe", "get_isins"],
    )
    if not get_universe:
        raise AttributeError(f"{block_module.__name__} no expone función de universo.")

    # -----------------------------
    # Selección de ISINs
    # -----------------------------
    if list_isin:
        isins = list_isin
    else:
        # RESTANTES es bloque residual: necesita conn para excluir ISINs ya clasificados
        universe = get_universe(df_master, conn) if heuristic_core == 0 else get_universe(df_master)
        isins = universe[:sample_size] if sample_size else universe

    # Excluir fondos con documento erróneo — aplica a todos los bloques
    wrong_doc = {
        r[0] for r in conn.execute(
            "SELECT ISIN FROM fund_kiid_metadata WHERE KIID_Status = 'WRONG_DOC'"
        ).fetchall() if r[0]
    }
    if wrong_doc:
        before = len(isins)
        isins = [i for i in isins if i not in wrong_doc]
        excluded = before - len(isins)
        if excluded:
            print(f"[{block_name}] {excluded} ISINs excluidos por KIID_Status=WRONG_DOC")

    total = len(isins)
    published = []

    for idx, isin in enumerate(isins, 1):
        _t_fund_start = time.perf_counter()
        _t_phases: dict = {}          # desglose por fase

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {block_name} {isin} ({idx}/{total})")

        kiid_text = kiid_meta = parsed = classification = pdf_bytes = None

        try:
            row = df_master[df_master["ISIN"] == isin]
            if row.empty:
                log_ingestion(conn, isin, f"{block_name}_MASTER", "WARN", "ISIN not in master")
                continue

            row0 = row.iloc[0]
            fund_name = row0.get("Fund_Name", "")
            mgmt = row0.get("Management_Company")

            _t0 = time.perf_counter()
            kiid_text, kiid_meta = get_kiid_for_isin(isin, str(master_excel_path), conn=conn)
            _t_phases["kiid_fetch"] = round((time.perf_counter() - _t0) * 1000)
            if not kiid_text:
                log_ingestion(conn, isin, f"{block_name}_KIID", "WARN", kiid_meta.get("KIID_Error"))

                #INICIO PARCHE DEBUG
                print(f"[DEBUG] ISIN {isin}")
                print(f"[DEBUG] len(kiid_text): {len(kiid_text) if kiid_text else 'None'}")

                pdf_bytes_dbg = kiid_meta.get("KIID_PDF_BYTES")
                if pdf_bytes_dbg:
                    print(f"[DEBUG] pdf_bytes size (MB): {len(pdf_bytes_dbg) / (1024*1024):.2f}")
                else:
                    print("[DEBUG] No pdf_bytes")
                #FIN PARCHE DEBUG

                continue

            pdf_bytes = kiid_meta.pop("KIID_PDF_BYTES", None)

            # Recuperar SRRI_Visual previo de la BD
            _srri_visual_prev  = None
            _srri_textual_prev = None
            _row = conn.execute(
                "SELECT SRRI_Visual, SRRI_Textual FROM fund_kiid_metadata "
                "WHERE ISIN=? AND KIID_Class=1",
                (isin,)
            ).fetchone()
            if _row:
                if _row[0] is not None:
                    _srri_visual_prev  = int(_row[0])
                if _row[1] is not None:
                    _srri_textual_prev = int(_row[1])

            # SRRI_Visual_Recovery: ELIMINADA (v16)
            # Razón: los únicos casos con pdf_bytes=None son CACHED y OK.
            # Para esos fondos, si SRRI_Visual=NULL en BD, la extracción visual
            # requiere una re-descarga → usar FORCE_REFRESH explícito vía SQL:
            #   UPDATE fund_kiid_metadata SET KIID_Status='FORCE_REFRESH'
            #   WHERE SRRI_Visual IS NULL AND KIID_Class=1;
            # La Recovery automática causaba descargas masivas no controladas
            # (throttling del servidor) al procesar RESTANTES con 2000+ fondos.

            _t0 = time.perf_counter()
            parsed = parse_kiid_generic(kiid_text, pdf_bytes=pdf_bytes,
                                        isin=isin, fund_name=fund_name,
                                        srri_visual_prev=_srri_visual_prev,
                                        srri_textual_prev=_srri_textual_prev)
            _t_phases["kiid_parse"] = round((time.perf_counter() - _t0) * 1000)
            pdf_bytes = None

            # ── Override universal: Estructurado ─────────────────────────
            # Antes de llamar al bloque, verificar si el fondo es un producto
            # estructurado. Si lo es, la naturaleza es Estructurado
            # independientemente del bloque de entrada.
            _t0 = time.perf_counter()
            _is_structured = is_structured_product(fund_name, kiid_text)

            classifier = dynamic_getattr(block_module, ["classify_fund"])
            if classifier:
                # restantes.py acepta benchmark_declared como Capa 3 opcional
                _bench = parsed.get("Benchmark_Declared")
                try:
                    classification = classifier(fund_name, kiid_text,
                                                benchmark_declared=_bench)
                except TypeError:
                    classification = classifier(fund_name, kiid_text)
            else:
                classification = {}

            # ── Override Fund_Nature si es estructurado ──────────────────
            if _is_structured:
                classification["Fund_Nature"] = "Estructurado"

            _t_phases["classify"] = round((time.perf_counter() - _t0) * 1000)

            # ── Enriquecer con fund_characterizer ────────────────────────
            # Solo ejecutar si hay atributos v3 por rellenar (Investment_Universe,
            # Accumulation_Policy, Currency_Hedged, etc.) o si la clasificación
            # es nueva/actualizada. Para fondos CACHED ya clasificados, comprueba
            # si los atributos v3 ya están en BD antes de invocar el characterizer.
            _kiid_status_c = kiid_meta.get("KIID_Status", "CACHED")
            _needs_char = (_kiid_status_c != "CACHED")  # siempre para re-descargas
            if not _needs_char:
                # Para CACHED: verificar si faltan atributos v3 en BD
                _v3_row = conn.execute(
                    "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged "
                    "FROM fund_master WHERE ISIN=?", (isin,)
                ).fetchone()
                _needs_char = (_v3_row is None or
                               any(v is None for v in (_v3_row or [])))

            if _needs_char:
                _srri_for_char = parsed.get("SRRI") or classification.get("SRRI")
                _char_result = characterize_fund(
                    fund_name=fund_name,
                    kiid_text=kiid_text,
                    fund_nature=classification.get("Fund_Nature") or "",
                    srri=int(_srri_for_char) if _srri_for_char else None,
                    pre_assigned={
                        k: v for k, v in classification.items()
                        if k not in ("Fund_Nature",) and v is not None
                    },
                )
                # Mezclar: el resultado del bloque tiene precedencia,
                # fund_characterizer solo rellena los None
                for _k, _v in _char_result.items():
                    if _k not in classification or classification[_k] is None:
                        classification[_k] = _v


            # --- validación estricta ---
            validate_classification_contract(
                classification=classification,
                block_name=block_name,
                isin=isin,
            )

            # --- copia defensiva ---
            classification = dict(classification)

            # --- warning si clasificación mínima ---
            if all(
                classification.get(k) is None
                for k in classification
                if k != "Fund_Nature"
            ):
                log_ingestion(
                    conn,
                    isin,
                    f"{block_name}_CLASSIFICATION",
                    "WARN",
                    "Clasificación mínima (solo Fund_Nature)",
                )


            fund_master_record = {
                # -------------------------
                # Identidad
                # -------------------------
                "ISIN": isin,
                "Fund_Name": fund_name,
                "Management_Company": mgmt,

                # -------------------------
                # Clasificación canónica
                # (producida por el bloque)
                # -------------------------
                "Fund_Nature": classification.get("Fund_Nature"),

                "Profile": classification.get("Profile"),
                "Type": classification.get("Type"),
                "Family": classification.get("Family"),
                "Style_Profile": classification.get("Style_Profile"),
                "Geography": classification.get("Geography"),
                "Theme": classification.get("Theme"),
                "Exposure_Bias":   classification.get("Exposure_Bias"),
                "Subtype":         classification.get("Subtype"),
                # canonico v2: Strategy e Is_ESG vienen del bloque
                "Strategy":        classification.get("Strategy") or _detect_strategy(
                    parsed.get("Replication_Method"),
                    classification.get("Subtype"),
                    (fund_name or "").lower(),
                ),
                "Is_ESG":          classification.get("Is_ESG", 0),
                # Benchmark_Type calculado con datos reales del parser
                "Benchmark_Type":  _detect_benchmark_type(
                    parsed.get("Benchmark_Declared"),
                    parsed.get("Replication_Method"),
                ),
                # Canonico v3 — fund_characterizer
                "Market_Cap_Focus":    classification.get("Market_Cap_Focus"),
                "Sector_Focus":        classification.get("Sector_Focus"),
                "Currency_Hedged":     classification.get("Currency_Hedged"),
                "Investment_Universe": classification.get("Investment_Universe"),

                # -------------------------
                # Heurística / estado
                # -------------------------
                "Heuristic_Block": block_name,
                "Heuristic_Core": heuristic_core,

                # -------------------------
                # Parsing documental (KIID)
                # -------------------------
                "SRRI": parsed.get("SRRI"),
                "Fund_Currency": parsed.get("Fund_Currency"),
                "Portfolio_Currency": parsed.get("Portfolio_Currency"),
                "Hedging_Policy": parsed.get("Hedging_Policy"),
                "Replication_Method": parsed.get("Replication_Method"),
                "Derivatives_Usage": parsed.get("Derivatives_Usage"),
                "Benchmark_Declared": parsed.get("Benchmark_Declared"),
                "Ongoing_Charge":     parsed.get("Ongoing_Charge"),
                # Accumulation_Policy: combinar characterizer (nombre) + kiid_parser (texto)
                "Accumulation_Policy": (
                    classification.get("Accumulation_Policy") or
                    parsed.get("Accumulation_Policy")
                ),
                "Entry_Fee_Pct":       parsed.get("Entry_Fee_Pct"),
                "Exit_Fee_Pct":        parsed.get("Exit_Fee_Pct"),
                "Sfdr_Article":        parsed.get("Sfdr_Article"),
                "Recommended_Holding_Period": parsed.get("Recommended_Holding_Period"),
                "Leverage_Used":       parsed.get("Leverage_Used"),
                "Liquidity_Profile":   parsed.get("Liquidity_Profile"),
                "Distribution_Frequency": parsed.get("Distribution_Frequency"),

                # -------------------------
                # QA / trazabilidad
                # -------------------------
                "Inference_Trace": parsed.get("Inference_Trace"),
                "SRRI_Quality_Flag": parsed.get("SRRI_Quality_Flag"),
                "Data_Quality_Flag": _derive_data_quality_flag(parsed),
            }


            # Is_ESG override: SFDR Art.8/9 es más fiable que keywords en nombre
            if parsed.get("Sfdr_Article") in (8, 9):
                fund_master_record["Is_ESG"] = 1

            _total_ms = round((time.perf_counter() - _t_fund_start) * 1000)
            _breakdown = "|".join(
                f"{k}:{v}ms" for k, v in _t_phases.items()
            ) if _t_phases else ""

            kiid_record = {
                "ISIN": isin,
                "KIID_URL": kiid_meta.get("KIID_URL"),                
                "KIID_Class": 1,
                "SRRI": parsed.get("SRRI"),
                "SRRI_Visual": parsed.get("SRRI_Visual"),
                "SRRI_Textual": parsed.get("SRRI_Textual"),
                "SRRI_Validation_Status": parsed.get("SRRI_Validation_Status"),

                #Benchmark_Declared
                #"Inference_Trace": parsed.get("Inference_Trace"),
                "Language": parsed.get("Language"),
                "Raw_KIID_Text": kiid_text,                
                "KIID_Published_Date": parsed.get("KIID_Published_Date"),
                "KIID_Downloaded_At": kiid_meta.get("KIID_Downloaded_At"),                
                "KIID_PDF_Hash": kiid_meta.get("KIID_PDF_Hash"),
                "KIID_Status": kiid_meta.get("KIID_Status"),
                "Processing_Time_Ms":   _total_ms,
                "Processing_Breakdown": _breakdown,
            }

            publish_fund(conn, fund_master_record, None, kiid_record)
            published.append(fund_master_record)

        except Exception as e:
            print(f"  [ERROR] {block_name} {isin}: {e}")
            log_ingestion(conn, isin, f"{block_name}_PROCESS", "ERROR", str(e))
            if stop_on_error:
                raise
        finally:
            # Timing summary — siempre visible, independientemente de CACHED vs descarga
            _elapsed = round((time.perf_counter() - _t_fund_start) * 1000)
            _is_download = kiid_meta and kiid_meta.get("KIID_Status") not in ("CACHED",)
            _label = "DESCARGA" if _is_download else "CACHED"
            if _elapsed > 2000 or _is_download:
                # Mostrar siempre descargas; mostrar CACHED solo si tarda >2s (anómalo)
                _phase_str = " | ".join(f"{k}:{v}ms" for k, v in _t_phases.items())                              if _t_phases else ""
                print(f"  [{_label}] {_elapsed}ms  {_phase_str}")
            kiid_text = kiid_meta = parsed = classification = pdf_bytes = None
            gc.collect()

    return published
