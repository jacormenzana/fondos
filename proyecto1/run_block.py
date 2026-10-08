# -*- coding: utf-8 -*-
r"""
run_block.py

Ejecutor simple para lanzar procesamiento de bloques.
Uso:
    Ubicarse en directorio c:\\desarrollo\\fondos\\proyecto1
    activar entorno des
    lanzar run_block desde entorno des

    # Universo desde DB (por defecto tras la implementacion de harvest):
    python run_block.py --block mixtos --master-db
    python run_block.py --nature-first --master-db

    # Universo desde Excel maestro (modo legacy / debug):
    python run_block.py --block mixtos --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --sample 5
    python run_block.py --block mixtos --master "c:\\data\\fondos\\in\\GestoresDeFondosv1.xlsx" --list-isin LU0232465467,LU1873127366
"""

import argparse
import importlib
from pathlib import Path

from core.pipeline import run_block, load_master_excel, load_master_db
from core.classify_utils import resolve_nature_vote  # noqa: F401 — validates OPT-B import
from core.fund_writer import get_connection, create_schema
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from shared.schema_checks import assert_schema_alignment

from core._db_utils import assert_eff_fields_alignment

BLOCKS_PACKAGE = "blocks"


def main():

    p = argparse.ArgumentParser()
    p.add_argument("--block", required=False, default=None,
                   help="Nombre del bloque (module name en blocks/). "
                        "Omit when using --nature-first.")
    p.add_argument("--nature-first", action="store_true", default=False,
                   help="OPT-B: single-pass nature-vote dispatch over ALL ISINs. "
                        "Mutually exclusive with --block.")
    p.add_argument("--family-nature-refresh", action="store_true", default=False,
                   help=(
                       "Recalcula los atributos DERIVADOS de la naturaleza (perfil, estilo, calidad crediticia, duracion...) "
                       "de los fondos ACTIVOS cuya Fund_Nature reescribio fund_family_builder (FAMILY_NATURE_CORRECTION sin "
                       "FAMILY_REFRESH_DONE posterior), conservando la naturaleza de la familia en vez de dejar que la "
                       "evidencia propia del fondo la revierta. Implica --nature-first; el conjunto se SELECCIONA, no se "
                       "pasa (incompatible con --block, --list-isin y --sample). Solo texto en cache, sin descargas; sin "
                       "pendientes no hace nada."
                   ))
    p.add_argument("--master", default=None,
                   help="Excel maestro (GestoresDeFondosv1.xlsx) — modo legacy")
    p.add_argument("--master-db", action="store_true", default=False,
                   help="Cargar universo de fondos desde db_document_catalogue (harvest DB)")
    p.add_argument("--sample", type=int, default=None,
                   help="sample size (opcional)")
    p.add_argument("--stop-on-error", action="store_true")
    p.add_argument("--list-isin", default=None,
                   help="Lista explícita de ISINs separada por comas (modo debug)")
    p.add_argument("--recompute-costs", action="store_true", default=False,
                   help=(
                       "Re-ejecuta el bloque de extracción de costes también en "
                       "fondos CACHED, sobre el texto ya almacenado en BD "
                       "(Raw_KIID_Text + DLA2_Table_Text). Sin este flag el bloque "
                       "solo corre con PDF recién descargado, de modo que una "
                       "corrección del extractor exigiría re-descargar los PDFs "
                       "afectados. No descarga nada. Usar tras cambiar "
                       "priips_cost_extractor / ucits_cost_extractor / "
                       "cost_table_parser, normalmente junto a --list-isin."
                   ))
    p.add_argument("--kiid-source", default="auto",
                   choices=["auto", "local", "remote"],
                   help=(
                       "Modalidad de carga del PDF KIID cuando la caché de texto "
                       "en BD no acierta (BL-KIID-LOCAL-FIRST):\n"
                       "  auto   (def.): local-first si KIID_LOCAL_FIRST_ENABLED, "
                       "con fallback a descarga remota.\n"
                       "  local  : fuerza lectura del repositorio local "
                       "(C:\\data\\fondos\\kiid), con fallback a remoto si no existe.\n"
                       "  remote : fuerza descarga por URL del maestro."
                   ))
    args = p.parse_args()

    if args.family_nature_refresh:
        if args.block or args.list_isin or args.sample:
            p.error("--family-nature-refresh selects its own funds: it excludes --block, --list-isin and --sample.")
        args.nature_first = True
    if not args.nature_first and not args.block:
        p.error("--block is required unless --nature-first is specified.")
    if args.nature_first and args.block:
        p.error("--block and --nature-first are mutually exclusive.")
    if not args.master and not args.master_db:
        p.error("Se requiere --master-db (recomendado) o --master <ruta_excel>.")
    if args.master and args.master_db:
        p.error("--master y --master-db son mutuamente excluyentes.")

    list_isin = None
    if args.list_isin:
        list_isin = [x.strip() for x in args.list_isin.split(",") if x.strip()]

    master_path = Path(args.master) if args.master else None


    # Cargar bloque (no depende de conn ni de maestro)
    if args.nature_first:
        block_mod = None
        print("[DEBUG] Modo: NATURE_FIRST (OPT-B) — universo completo del maestro")
    else:
        print(f"[DEBUG] Carga bloque: {BLOCKS_PACKAGE}.{args.block}")
        block_mod = importlib.import_module(f"{BLOCKS_PACKAGE}.{args.block}")
        print(f"[DEBUG] block_mod: {block_mod}")

    # Conexión y schema (idempotente) — abierta antes del maestro para --master-db
    conn = get_connection()
    # Postgres schema is provisioned once via db/pg/00_roles_schemas.sql .. 40_matviews.sql
    # (see docker-compose.yml) — create_schema() is SQLite-only by design (guards itself; see
    # its own docstring) and psycopg3 Connection has no isolation_level attribute to reset.
    # Found live 2026-09-22 (migration Stage 9): run_block.py's CLI entry point had never
    # actually been invoked end-to-end with --backend postgres before — every earlier stage
    # tested the ported functions directly (pg_conn fixtures), not this main()'s own startup
    # sequence, so this backend-conditional skip was simply missing until this first real run.
    pass
    assert_schema_alignment(conn)
    assert_eff_fields_alignment(conn)

    # Cargar maestro (DB o Excel)
    if args.master_db:
        df_master = load_master_db(conn)
    else:
        df_master = load_master_excel(master_path)
    print(f"[DEBUG] Maestro cargado: {df_master.shape}")

    # Ejecutar bloque / pasada nature-first
    published = run_block(
        block_mod,
        df_master,
        conn,
        master_excel_path=master_path,
        sample_size=args.sample,
        stop_on_error=args.stop_on_error,
        list_isin=list_isin,
        kiid_source=args.kiid_source,
        nature_first=args.nature_first,
        recompute_costs=args.recompute_costs,
        family_refresh=args.family_nature_refresh,
    )

    _mode = "FAMILY_REFRESH" if args.family_nature_refresh else ("NATURE_FIRST" if args.nature_first else args.block)
    print(f"Bloque/modo {_mode} procesado. Registros publicados: {len(published)}")


    # BL-53/56/57: Barrido global post-pipeline (Principio #1 + #2)
    # Cubre fondos no procesados en este ciclo (KIID_Status=WRONG_DOC,
    # excluidos del bloque, etc.) que conservan valores stale en BD.
    from core.pipeline import run_global_normalization
    run_global_normalization(conn)

    # FIX-UNIVERSE-RECON-1: universe membership reconciliation (v23).
    # Marks fund_master rows in/out of the current harvest universe; idempotent.
    # Not COALESCE-protected: regenerated every cycle from the loaded master.
    from core.fund_writer import reconcile_universe_membership
    reconcile_universe_membership(
        conn,
        df_master["ISIN"].dropna().astype(str).unique().tolist(),
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    from shared.backlog_client import capture_exceptions
    with capture_exceptions(object_name="run_block.py", object_type="JOB"):
        main()
