# -*- coding: utf-8 -*-
r"""
run_block.py

Ejecutor simple para lanzar procesamiento de bloques.
Uso:
    Ubicarse en directorio c:\\desarrollo\\fondos\\proyecto1
    activar entorno des
    lanzar run_block desde entorno des

    # Universo desde el catalogo del ultimo harvest (unico modo):
    python run_block.py --nature-first --master-db
    python run_block.py --nature-first --master-db --list-isin LU0232465467,LU1873127366
    python run_block.py --family-nature-refresh --master-db

    # RETIRADOS (FND-0247): --block <nombre> y --master <Excel>. El camino por bloques con el Excel maestro reescribia
    # etiquetas en la BD en vivo (heuristic_block, management_company, naturaleza) y reactivaba fondos retirados.
    # Se rechazan antes de abrir ninguna conexion; no hay modo simulado ni anulacion.
"""

import argparse

from core.pipeline import run_block, load_master_db
from core.classify_utils import resolve_nature_vote  # noqa: F401 — validates OPT-B import
from core.fund_writer import get_connection, create_schema
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from shared.schema_checks import assert_schema_alignment

from core._db_utils import assert_eff_fields_alignment

RETIRED_MESSAGE = (
    "--block and --master <Excel> are RETIRED (FND-0247): the by-block path classified with the legacy paradigm and took its universe and labels "
    "from the Excel master, which rewrote live fund_master rows and re-activated retired funds. Use --nature-first --master-db "
    "[--list-isin A,B] (or --family-nature-refresh --master-db). Nothing was run and no connection was opened.")


def main():

    p = argparse.ArgumentParser()
    p.add_argument("--block", required=False, default=None,
                   help="RETIRED (FND-0247): refused before any connection is opened. Use --nature-first --master-db.")
    p.add_argument("--nature-first", action="store_true", default=False,
                   help="OPT-B: single-pass nature-vote dispatch over ALL ISINs (the only classification mode).")
    p.add_argument("--family-nature-refresh", action="store_true", default=False,
                   help=(
                       "Recalcula los atributos DERIVADOS de la naturaleza (perfil, estilo, calidad crediticia, duracion...) "
                       "de los fondos ACTIVOS cuya Fund_Nature reescribio fund_family_builder (FAMILY_NATURE_CORRECTION sin "
                       "FAMILY_REFRESH_DONE posterior), conservando la naturaleza de la familia en vez de dejar que la "
                       "evidencia propia del fondo la revierta. Implica --nature-first; el conjunto se SELECCIONA, no se "
                       "pasa (incompatible con --list-isin y --sample). Solo texto en cache, sin descargas; sin "
                       "pendientes no hace nada."
                   ))
    p.add_argument("--master", default=None,
                   help="RETIRED (FND-0247): the Excel master is refused before any connection is opened. Use --master-db.")
    p.add_argument("--master-db", action="store_true", default=False,
                   help="Universo de fondos desde db_document_catalogue (ultimo harvest). Obligatorio.")
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

    # RETIRED (FND-0247). The by-block path classified with the legacy paradigm and took its universe and labels from the Excel master; one accidental
    # run (2026-10-08) rewrote 37 live fund_master rows and re-activated ~277 retired funds through its universe reconcile. It is refused BEFORE any
    # connection is opened: there is no dry-run to fall back to and no override, because the only safe version of a write path that must never
    # run is one that cannot start.
    if args.block or args.master:
        p.error(RETIRED_MESSAGE)
    if args.family_nature_refresh:
        if args.list_isin or args.sample:
            p.error("--family-nature-refresh selects its own funds: it excludes --list-isin and --sample.")
        args.nature_first = True
    if not args.nature_first:
        p.error("--nature-first (or --family-nature-refresh) is required.")
    if not args.master_db:
        p.error("--master-db is required: the universe comes from the latest harvest (db_document_catalogue), never from an Excel.")

    list_isin = None
    if args.list_isin:
        list_isin = [x.strip() for x in args.list_isin.split(",") if x.strip()]

    master_path = None
    block_mod = None
    print("[DEBUG] Modo: NATURE_FIRST (OPT-B) — universo completo del maestro")

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

    # Cargar maestro: siempre el catalogo del ultimo harvest
    df_master = load_master_db(conn)
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

    _mode = "FAMILY_REFRESH" if args.family_nature_refresh else "NATURE_FIRST"
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
