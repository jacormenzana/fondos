# proyecto1/src/analysis/export_p1.py
# -*- coding: utf-8 -*-
"""
Exportacion de tablas del Proyecto 1 a Excel.

Genera un fichero Excel con las tablas principales gestionadas por P1:
  Hoja 1_FundMaster  — Universo completo de fondos clasificados
  Hoja 2_KIIDMetadata — Metadatos y estado de KIIDs procesados

Notas de diseño:
  - Raw_KIID_Text se excluye por defecto (texto completo OCR, muy pesado).
    Usar --include-kiid-text para incluirlo (util para analisis de parsing).
  - Inference_Trace se excluye por defecto (cadena de trazabilidad interna).
  - NAV mensual NO se exporta aqui — es dominio de P2.
  - Nomenclatura: p1_export_YYYYMMDD.xlsx  /  p1_export_<block>_YYYYMMDD.xlsx

Uso:
    cd c:/desarrollo/fondos
    python -m proyecto1.src.analysis.export_p1
    python -m proyecto1.src.analysis.export_p1 --output c:/ruta/personalizada
    python -m proyecto1.src.analysis.export_p1 --include-kiid-text
    python -m proyecto1.src.analysis.export_p1 --block renta_variable
    python -m proyecto1.src.analysis.export_p1 --block monetarios --include-kiid-text
"""

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

from shared.config import DATA_DIR

# Directorio de salida: c:/desarrollo/fondos/out/export/
_OUT_DIR: Path = DATA_DIR.parent / 'out'
from shared.export_tables import TableExportConfig, export_tables, dated_filename

# ============================================================
# Directorio de salida por defecto
# ============================================================
EXPORT_DIR: Path = _OUT_DIR / "export"

# ============================================================
# Configuracion de tablas P1
# ============================================================

# Valores canonicos de heuristic_block (mirror de blocks/*.py + bat)
VALID_BLOCKS: frozenset = frozenset({
    "MONETARIOS", "RF_CORTO", "RF_FLEXIBLE",
    "RENTA_VARIABLE", "MIXTOS", "ALTERNATIVOS", "RESTANTES",
})


def get_tables(
    include_kiid_text: bool = False,
    block_filter=None,
) -> list:
    """
    Devuelve la configuracion de exportacion de P1.

    include_kiid_text: si True, incluye Raw_KIID_Text en fund_kiid_metadata.
                       Util para analisis del parser OCR pero genera ficheros
                       mucho mas grandes (~500 MB vs ~10 MB).
    block_filter:      si se indica, restringe las 5 hojas al subconjunto de
                       ISINs (o, en fund_families, de familias con al menos un
                       fondo) cuyo heuristic_block coincide con ese valor.
                       Debe ser uno de VALID_BLOCKS; se valida antes de llamar.
    """
    kiid_exclude = [] if include_kiid_text else ["Raw_KIID_Text"]

    # Clausulas WHERE para filtrado por bloque
    fm_where   = None
    kiid_where = None
    family_where = None
    if block_filter:
        # Interpolacion segura: block_filter ya validado contra VALID_BLOCKS en export_p1()
        fm_where   = f"heuristic_block = '{block_filter}'"
        # FND-0065 (2026-09-27): fund_benchmarks/fund_families do NOT have a heuristic_block
        # column (only fund_master does) -- fm_where applied to them directly crashed with
        # "column heuristic_block does not exist" (reproduced live). fund_benchmarks is keyed by
        # ISIN, same subquery shape as kiid_where; fund_families has no ISIN at all (it's an
        # aggregate keyed by family_id/fund_family_id), so it needs its own, differently-shaped one.
        kiid_where = (
            f"ISIN IN (SELECT ISIN FROM fund_master WHERE heuristic_block = '{block_filter}')"
        )
        family_where = (
            f"family_id IN (SELECT fund_family_id FROM fund_master "
            f"WHERE heuristic_block = '{block_filter}' AND fund_family_id IS NOT NULL)"
        )

    return [
        TableExportConfig(
            table="fund_master",
            sheet_name="1_FundMaster",
            exclude_cols=["Inference_Trace"],
            order_by="Fund_Nature, Management_Company, Fund_Name",
            where=fm_where,
        ),
        TableExportConfig(
            table="fund_kiid_metadata",
            sheet_name="2_KIIDMetadata",
            exclude_cols=kiid_exclude,
            order_by="ISIN",
            where=kiid_where,
        ),
        TableExportConfig(
            table="fund_benchmarks",
            sheet_name="3_FundBenchmarks",
            exclude_cols=[],
            order_by="ISIN",
            where=kiid_where,
        ),
        TableExportConfig(
            table="fund_families",
            sheet_name="4_FundFamilies",
            exclude_cols=[],
            order_by="family_id",
            where=family_where,
        ),
        TableExportConfig(
            table="fund_cost_schedule",
            sheet_name="5_FundCostSchedule",
            exclude_cols=[],
            order_by="ISIN",
            where=kiid_where,
        ),
        
        
    ]


# ============================================================
# Funcion principal
# ============================================================

def export_p1(
    output_dir=None,
    include_kiid_text: bool = False,
    block=None,
):
    """
    Exporta las tablas de P1 a un fichero Excel con fecha en el nombre.

    Parametros:
        output_dir:        directorio de salida (default: out/export/)
        include_kiid_text: incluir columna Raw_KIID_Text (default: False)
        block:             si se indica, filtra por heuristic_block.
                           Debe ser uno de VALID_BLOCKS; abort con ValueError si no.

    Devuelve la ruta del fichero generado.
    """
    print(f"DEBUG _ROOT     = {_ROOT}")
    print(f"DEBUG EXPORT_DIR= {EXPORT_DIR}")

    # Validar block contra whitelist (safe interpolation guard)
    if block is not None:
        # FND-0065 (2026-09-27): heuristic_block is stored uppercase (RENTA_VARIABLE, ...), but
        # --block's own documented usage examples are lowercase ("--block renta_variable"). Only
        # the VALIDATION used .upper() -- the value actually interpolated into the WHERE clauses
        # stayed as typed, so a correctly-validated, correctly-documented invocation silently
        # matched 0 rows on every sheet (reproduced live). Normalize once, here, and use the
        # normalized value everywhere below.
        if block.upper() not in VALID_BLOCKS:
            raise ValueError(
                f"--block '{block}' no valido. "
                f"Valores permitidos: {sorted(VALID_BLOCKS)}"
            )
        block = block.upper()
        print(f"Filtro activo: heuristic_block = '{block}'")

    output_dir = Path(output_dir) if output_dir else EXPORT_DIR

    prefix   = f"p1_export_{block.lower()}" if block else "p1_export"
    out_path = output_dir / dated_filename(prefix)
    tables   = get_tables(include_kiid_text=include_kiid_text, block_filter=block)

    return export_tables(
        tables=tables,
        output_path=out_path,
        verbose=True,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    from shared.backlog_client import install_excepthook
    install_excepthook(object_name="export_p1.py")          # unhandled failure -> backlog ticket
    parser = argparse.ArgumentParser(
        description="Exportacion de tablas P1 a Excel"
    )
    parser.add_argument(
        "--output", default=None,
        help="Directorio de salida (default: out/export/)"
    )
    parser.add_argument(
        "--include-kiid-text", action="store_true",
        help="Incluir columna Raw_KIID_Text (fichero mas grande)"
    )
    parser.add_argument(
        "--block", default=None,
        metavar="BLOCK",
        help=(
            "Filtrar export por heuristic_block. "
            f"Valores: {sorted(VALID_BLOCKS)}"
        ),
    )
    args = parser.parse_args()

    export_p1(
        output_dir=args.output,
        include_kiid_text=args.include_kiid_text,
        block=args.block,
    )
