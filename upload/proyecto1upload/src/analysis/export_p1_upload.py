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

from shared.config import DB_PATH, DATA_DIR

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
    block_filter:      si se indica, restringe ambas hojas al subconjunto de
                       ISINs cuyo heuristic_block coincide con ese valor.
                       Debe ser uno de VALID_BLOCKS; se valida antes de llamar.
    """
    kiid_exclude = [] if include_kiid_text else ["Raw_KIID_Text"]

    # Clausulas WHERE para filtrado por bloque
    fm_where   = None
    kiid_where = None
    if block_filter:
        # Interpolacion segura: block_filter ya validado contra VALID_BLOCKS en export_p1()
        fm_where   = f"heuristic_block = '{block_filter}'"
        kiid_where = (
            f"ISIN IN (SELECT ISIN FROM fund_master WHERE heuristic_block = '{block_filter}')"
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
    ]


# ============================================================
# Funcion principal
# ============================================================

def export_p1(
    output_dir=None,
    db_path=None,
    include_kiid_text: bool = False,
    block=None,
):
    """
    Exporta las tablas de P1 a un fichero Excel con fecha en el nombre.

    Parametros:
        output_dir:        directorio de salida (default: out/export/)
        db_path:           ruta a la BD (default: DB_PATH de shared/config)
        include_kiid_text: incluir columna Raw_KIID_Text (default: False)
        block:             si se indica, filtra por heuristic_block.
                           Debe ser uno de VALID_BLOCKS; abort con ValueError si no.

    Devuelve la ruta del fichero generado.
    """
    print(f"DEBUG _ROOT     = {_ROOT}")
    print(f"DEBUG DB_PATH   = {DB_PATH}")
    print(f"DEBUG EXPORT_DIR= {EXPORT_DIR}")

    # Validar block contra whitelist (safe interpolation guard)
    if block is not None:
        if block.upper() not in VALID_BLOCKS:
            raise ValueError(
                f"--block '{block}' no valido. "
                f"Valores permitidos: {sorted(VALID_BLOCKS)}"
            )
        print(f"Filtro activo: heuristic_block = '{block}'")

    output_dir = Path(output_dir) if output_dir else EXPORT_DIR
    db_path    = Path(db_path)    if db_path    else DB_PATH

    prefix   = f"p1_export_{block.lower()}" if block else "p1_export"
    out_path = output_dir / dated_filename(prefix)
    tables   = get_tables(include_kiid_text=include_kiid_text, block_filter=block)

    return export_tables(
        tables=tables,
        output_path=out_path,
        db_path=db_path,
        verbose=True,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Exportacion de tablas P1 a Excel"
    )
    parser.add_argument(
        "--output", default=None,
        help="Directorio de salida (default: out/export/)"
    )
    parser.add_argument(
        "--db", default=None,
        help="Ruta alternativa a fondos.sqlite"
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
        db_path=args.db,
        include_kiid_text=args.include_kiid_text,
        block=args.block,
    )
