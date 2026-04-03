# core/pipeline_cache.py  — v17
# -*- coding: utf-8 -*-
"""
Motor central del pipeline de clasificación de fondos (P1).

Responsabilidades:
  - Orquestar la ejecución de un bloque de clasificación sobre un universo
    de ISINs: ingesta KIID, parsing, clasificación, caracterización, escritura.
  - Ensamblar el fund_master_record completo antes de publicar en SQLite.
  - Gestionar métricas de tiempo (telemetría por fondo y por fase).

Cambios v17:
  - fund_master_record: añadidos Investment_Focus (classification),
    Credit_Quality (classification), Fee_Known_Flag (parsed).
  - _needs_char SELECT ampliado: incluye Investment_Focus y Credit_Quality
    para detectar fondos CACHED con atributos v17 aún sin poblar.
"""

import time
from typing import List, Optional, Dict, Any

from core.sqlite_writer import publish_fund, log_ingestion
from core.kiid_parser import parse_kiid_generic
from core.fund_characterizer import characterize_fund


# ============================================================
# Helpers internos
# ============================================================

def _detect_strategy(replication_method: Optional[str],
                     subtype: Optional[str],
                     name_l: str) -> Optional[str]:
    """Deriva la estrategia de gestión desde atributos disponibles."""
    if replication_method in ("Physical", "Synthetic", "Sampling"):
        return "Passive"
    if subtype in ("Index", "ETF"):
        return "Index"
    if any(k in name_l for k in ["quant", "systematic", "factor", "smart beta"]):
        return "Quant/Systematic"
    if any(k in name_l for k in ["index", "índice", "indice", "tracker", "etf"]):
        return "Index"
    return "Active"


def _detect_benchmark_type(benchmark_declared: Optional[str],
                            replication_method: Optional[str]) -> Optional[str]:
    """Determina el tipo de relación con el benchmark."""
    if not benchmark_declared or benchmark_declared == "NO_BENCHMARK":
        return "None"
    if replication_method in ("Physical", "Synthetic", "Sampling"):
        return "Target"
    return "Reference"


def _derive_data_quality_flag(parsed: Dict[str, Any]) -> str:
    """Flag de calidad global del registro basado en gaps críticos."""
    srri = parsed.get("SRRI")
    lang = parsed.get("Language")
    if srri is None and lang is None:
        return "ERROR"
    if srri is None or lang is None:
        return "WARN"
    return "OK"


def validate_classification_contract(
    classification: Dict[str, Any],
    block_name: str,
    isin: str,
) -> None:
    """
    Valida el contrato mínimo de clasificación.
    Lanza ValueError si Fund_Nature es None (error crítico).
    """
    if not classification.get("Fund_Nature"):
        raise ValueError(
            f"[{block_name}] ISIN={isin}: Fund_Nature es None. "
            "El bloque debe asignar siempre una naturaleza."
        )


# ============================================================
# run_block — motor principal
# ============================================================

def run_block(
    block_mod,
    df_master,
    conn,
    master_excel_path=None,
    sample_size: Optional[int] = None,
    stop_on_error: bool = False,
    list_isin: Optional[List[str]] = None,
) -> List[str]:
    """
    Ejecuta un bloque de clasificación completo sobre su universo de ISINs.

    Parámetros:
        block_mod:         módulo del bloque (ej. blocks.renta_variable)
        df_master:         DataFrame con el maestro de fondos
        conn:              conexión SQLite activa
        master_excel_path: ruta al Excel maestro (para io.py)
        sample_size:       limitar a N fondos (debug)
        stop_on_error:     si True, relanza excepciones
        list_isin:         lista explícita de ISINs (modo debug)

    Devuelve lista de ISINs publicados correctamente.
    """
    from core.io import get_kiid_for_isin

    block_name = getattr(block_mod, "BLOCK_NAME", block_mod.__name__)

    # ── Universo del bloque ──────────────────────────────────────────────────
    if list_isin:
        isins = list_isin
    else:
        isins = block_mod.get_universe_isins(df_master)
        if sample_size:
            isins = isins[:sample_size]

    print(f"\n[{block_name}] Universo: {len(isins)} fondos")

    published: List[str] = []
    errors: List[str] = []

    for idx, isin in enumerate(isins, 1):

        _t_fund_start = time.perf_counter()
        _t_phases: Dict[str, int] = {}

        try:
            # ── Datos básicos del maestro ────────────────────────────────────
            row = df_master[df_master["ISIN"] == isin]
            if row.empty:
                continue
            row = row.iloc[0]
            fund_name = str(row.get("Nombre", row.get("Fund_Name", "")))
            mgmt      = str(row.get("Gestora", row.get("Management_Company", "")))

            # ── Paso 1: obtener KIID ─────────────────────────────────────────
            _t0 = time.perf_counter()
            kiid_meta = get_kiid_for_isin(conn, isin, fund_name)
            _t_phases["kiid_fetch"] = round((time.perf_counter() - _t0) * 1000)

            kiid_text   = kiid_meta.get("Raw_KIID_Text") or ""
            kiid_status = kiid_meta.get("KIID_Status", "NOT_FOUND")

            # ── Paso 2: parsing KIID ─────────────────────────────────────────
            _t0 = time.perf_counter()
            parsed = parse_kiid_generic(
                kiid_text=kiid_text,
                fund_name=fund_name,
                kiid_status=kiid_status,
                srri_prev=kiid_meta.get("SRRI"),
                srri_textual_prev=kiid_meta.get("SRRI_Textual"),
            )
            _t_phases["kiid_parse"] = round((time.perf_counter() - _t0) * 1000)

            # ── Paso 3: SFDR imputation ──────────────────────────────────────
            # Si el parser no extrajo Sfdr_Article, imputar Art.6 como default
            # y Art.8 si el fondo tiene señales ESG en el nombre.
            if not parsed.get("Sfdr_Article"):
                from core.fund_characterizer import detect_is_esg
                is_esg_name = detect_is_esg(fund_name)
                parsed["Sfdr_Article"] = "8" if is_esg_name else "6"

            # ── Paso 4: clasificación del bloque ─────────────────────────────
            _t0 = time.perf_counter()
            classification = block_mod.classify(
                isin=isin,
                fund_name=fund_name,
                kiid_text=kiid_text,
                parsed=parsed,
                conn=conn,
            )
            _t_phases["classify"] = round((time.perf_counter() - _t0) * 1000)

            # ── Paso 5: fund_characterizer (atributos secundarios) ────────────
            # Solo si el fondo no estaba ya CACHED con atributos v17 completos.
            _kiid_status_c = kiid_meta.get("KIID_Status", "CACHED")
            _needs_char = (_kiid_status_c != "CACHED")  # siempre para re-descargas

            if not _needs_char:
                # Para CACHED: verificar si faltan atributos v3/v17 en BD
                _v3_row = conn.execute(
                    "SELECT Investment_Universe, Accumulation_Policy, "
                    "Currency_Hedged, Investment_Focus, Credit_Quality "
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
                # Mezclar: el bloque tiene precedencia,
                # fund_characterizer solo rellena los None
                for _k, _v in _char_result.items():
                    if _k not in classification or classification[_k] is None:
                        classification[_k] = _v

            # ── Validación del contrato ───────────────────────────────────────
            validate_classification_contract(classification, block_name, isin)
            classification = dict(classification)  # copia defensiva

            if all(
                classification.get(k) is None
                for k in classification
                if k != "Fund_Nature"
            ):
                log_ingestion(conn, isin, f"{block_name}_CLASSIFICATION",
                              "WARN", "Clasificación mínima (solo Fund_Nature)")

            # ── Ensamblar fund_master_record (v17) ────────────────────────────
            fund_master_record = {
                # Identidad
                "ISIN":               isin,
                "Fund_Name":          fund_name,
                "Management_Company": mgmt,

                # Clasificación canónica (producida por el bloque)
                "Fund_Nature":   classification.get("Fund_Nature"),
                "Profile":       classification.get("Profile"),
                "Type":          classification.get("Type"),
                "Family":        classification.get("Family"),
                "Style_Profile": classification.get("Style_Profile"),
                "Geography":     classification.get("Geography"),
                "Theme":         classification.get("Theme"),
                "Exposure_Bias": classification.get("Exposure_Bias"),
                "Subtype":       classification.get("Subtype"),

                # Estrategia e Is_ESG derivados
                "Strategy": classification.get("Strategy") or _detect_strategy(
                    parsed.get("Replication_Method"),
                    classification.get("Subtype"),
                    (fund_name or "").lower(),
                ),
                "Is_ESG": classification.get("Is_ESG", 0),

                # Benchmark_Type calculado con datos reales del parser
                "Benchmark_Type": _detect_benchmark_type(
                    parsed.get("Benchmark_Declared"),
                    parsed.get("Replication_Method"),
                ),

                # Atributos v3 — fund_characterizer
                "Market_Cap_Focus":   classification.get("Market_Cap_Focus"),
                "Sector_Focus":       classification.get("Sector_Focus"),
                "Currency_Hedged":    classification.get("Currency_Hedged"),
                "Investment_Universe": classification.get("Investment_Universe"),

                # Atributos v17 — fund_characterizer
                "Investment_Focus": classification.get("Investment_Focus"),
                "Credit_Quality":   classification.get("Credit_Quality"),

                # Heurística / estado
                "Heuristic_Block": block_name,
                "Heuristic_Core":  int(bool(classification.get("Fund_Nature"))),

                # Parsing documental (KIID)
                "SRRI":            parsed.get("SRRI"),
                "Fund_Currency":   parsed.get("Fund_Currency"),
                "Portfolio_Currency": parsed.get("Portfolio_Currency"),
                "Hedging_Policy":  parsed.get("Hedging_Policy"),
                "Replication_Method": parsed.get("Replication_Method"),
                "Derivatives_Usage":  parsed.get("Derivatives_Usage"),
                "Benchmark_Declared": parsed.get("Benchmark_Declared"),
                "Ongoing_Charge":     parsed.get("Ongoing_Charge"),

                # Accumulation_Policy: characterizer (nombre) tiene precedencia
                # sobre kiid_parser (texto) para maximizar cobertura
                "Accumulation_Policy": (
                    classification.get("Accumulation_Policy") or
                    parsed.get("Accumulation_Policy")
                ),
                "Entry_Fee_Pct":       parsed.get("Entry_Fee_Pct"),
                "Exit_Fee_Pct":        parsed.get("Exit_Fee_Pct"),
                # v17: Fee_Known_Flag viene del parser (no del classificador)
                "Fee_Known_Flag":      parsed.get("Fee_Known_Flag"),

                "Sfdr_Article":           parsed.get("Sfdr_Article"),
                "Recommended_Holding_Period": parsed.get("Recommended_Holding_Period"),
                "Leverage_Used":          parsed.get("Leverage_Used"),
                "Liquidity_Profile":      parsed.get("Liquidity_Profile"),
                "Distribution_Frequency": parsed.get("Distribution_Frequency"),

                # QA / trazabilidad
                "Inference_Trace":  parsed.get("Inference_Trace"),
                "SRRI_Quality_Flag": parsed.get("SRRI_Quality_Flag"),
                "Data_Quality_Flag": _derive_data_quality_flag(parsed),
            }

            # Is_ESG override: SFDR Art.8/9 es más fiable que keywords en nombre
            if parsed.get("Sfdr_Article") in ("8", "9", 8, 9):
                fund_master_record["Is_ESG"] = 1

            # ── Telemetría ────────────────────────────────────────────────────
            _total_ms = round((time.perf_counter() - _t_fund_start) * 1000)
            _breakdown = "|".join(
                f"{k}:{v}ms" for k, v in _t_phases.items()
            ) if _t_phases else ""

            # ── KIID record ───────────────────────────────────────────────────
            kiid_record = {
                "ISIN":                  isin,
                "KIID_Class":            kiid_meta.get("KIID_Class", 1),
                "KIID_URL":              kiid_meta.get("KIID_URL"),
                "KIID_PDF_Hash":         kiid_meta.get("KIID_PDF_Hash"),
                "KIID_Status":           kiid_status,
                "Language":              parsed.get("Language"),
                "Raw_KIID_Text":         kiid_text or None,
                "KIID_Published_Date":   parsed.get("KIID_Published_Date"),
                "KIID_Downloaded_At":    kiid_meta.get("KIID_Downloaded_At"),
                "SRRI":                  parsed.get("SRRI"),
                "SRRI_Visual":           parsed.get("SRRI_Visual"),
                "SRRI_Textual":          parsed.get("SRRI_Textual"),
                "SRRI_Validation_Status": parsed.get("SRRI_Validation_Status"),
                "Processing_Time_Ms":    _total_ms,
                "Processing_Breakdown":  _breakdown,
            }

            # ── Publicar ──────────────────────────────────────────────────────
            publish_fund(conn, fund_master_record, kiid_record=kiid_record)
            published.append(isin)

            if idx % 100 == 0:
                print(f"  [{block_name}] {idx}/{len(isins)} procesados "
                      f"({len(published)} publicados)")

        except Exception as exc:
            errors.append(isin)
            try:
                log_ingestion(conn, isin, f"{block_name}_ERROR", "ERROR", str(exc))
            except Exception:
                pass
            if stop_on_error:
                raise
            print(f"  [{block_name}] ERROR ISIN={isin}: {exc}")

    print(f"\n[{block_name}] Completado: {len(published)} publicados, "
          f"{len(errors)} errores")
    if errors:
        print(f"  ISINs con error: {errors[:10]}{'...' if len(errors)>10 else ''}")

    return published


# ============================================================
# load_master_excel (helper para run_block.py)
# ============================================================

def load_master_excel(master_path):
    """Carga el Excel maestro de fondos."""
    import pandas as pd
    df = pd.read_excel(master_path, dtype=str)
    df = df.fillna("")
    return df
