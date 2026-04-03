# scripts/restore_kiid_text.py
# -*- coding: utf-8 -*-
"""
Restaura Raw_KIID_Text en fund_kiid_metadata desde un Excel de referencia.

Uso:
    cd c:/desarrollo/fondos
    python scripts/restore_kiid_text.py
    python scripts/restore_kiid_text.py --dry-run
    python scripts/restore_kiid_text.py --ref "ruta/al/p1_output_sqlite.xlsx"

Qué hace:
    Para cada ISIN de la referencia que tiene Raw_KIID_Text:
    - Si el fondo existe en fund_kiid_metadata con Raw_KIID_Text=NULL → restaura
    - Si el fondo existe con texto ya → salta (no sobreescribe)
    - Si el fondo no existe en fund_kiid_metadata → inserta registro mínimo

No modifica fund_master ni ninguna otra tabla.
"""

import sys
import sqlite3
import argparse
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def restore_kiid_text(
    db_path: Path,
    ref_path: Path,
    dry_run: bool = False,
) -> None:
    # ── Cargar referencia ────────────────────────────────────────────────
    print(f"Referencia: {ref_path}")
    ref = pd.read_excel(str(ref_path), sheet_name="fund_kiid_metadata")
    
    con_texto = ref[ref['Raw_KIID_Text'].notna()].copy()
    print(f"Fondos con Raw_KIID_Text en referencia: {len(con_texto)}/{len(ref)}")

    # ── Conectar a BD ─────────────────────────────────────────────────────
    print(f"BD: {db_path}")
    conn = sqlite3.connect(str(db_path))

    # Estado actual de la BD
    existing = conn.execute(
        "SELECT ISIN, Raw_KIID_Text IS NOT NULL as has_text FROM fund_kiid_metadata"
    ).fetchall()
    existing_dict = {r[0]: bool(r[1]) for r in existing}
    
    in_db_sin_texto = sum(1 for v in existing_dict.values() if not v)
    in_db_con_texto = sum(1 for v in existing_dict.values() if v)
    not_in_db       = sum(1 for isin in con_texto['ISIN'] if isin not in existing_dict)
    
    print(f"\nEstado BD actual ({len(existing_dict)} registros):")
    print(f"  Con Raw_KIID_Text:    {in_db_con_texto}")
    print(f"  Sin Raw_KIID_Text:    {in_db_sin_texto}")
    print(f"  Sin registro en BD:   {not_in_db}")

    # ── Preparar actualizaciones ──────────────────────────────────────────
    updates      = []  # (text, isin) — ya están en BD sin texto
    inserts      = []  # registros completos — no están en BD
    ya_tienen    = 0
    no_en_master = 0

    # ISINs que existen en fund_master (no insertamos huérfanos)
    master_isins = {r[0] for r in conn.execute("SELECT ISIN FROM fund_master").fetchall()}

    for _, row in con_texto.iterrows():
        isin = row['ISIN']
        text = row['Raw_KIID_Text']

        if isin in existing_dict:
            if existing_dict[isin]:
                ya_tienen += 1  # ya tiene texto, no tocar
            else:
                updates.append((text, isin))
        else:
            # No está en fund_kiid_metadata — solo insertar si está en fund_master
            if isin in master_isins:
                inserts.append({
                    'ISIN':                 isin,
                    'KIID_Class':           row.get('KIID_Class'),
                    'KIID_URL':             row.get('KIID_URL'),
                    'KIID_PDF_Hash':        row.get('KIID_PDF_Hash'),
                    'KIID_Status':          row.get('KIID_Status'),
                    'Language':             row.get('Language'),
                    'Raw_KIID_Text':        text,
                    'KIID_Published_Date':  row.get('KIID_Published_Date'),
                    'KIID_Downloaded_At':   row.get('KIID_Downloaded_At'),
                    'SRRI':                 row.get('SRRI'),
                    'SRRI_Visual':          row.get('SRRI_Visual'),
                    'SRRI_Textual':         row.get('SRRI_Textual'),
                    'SRRI_Validation_Status': row.get('SRRI_Validation_Status'),
                })
            else:
                no_en_master += 1

    print(f"\nAcciones a ejecutar:")
    print(f"  UPDATE (restaurar texto):  {len(updates)}")
    print(f"  INSERT (registro nuevo):   {len(inserts)}")
    print(f"  SKIP (ya tienen texto):    {ya_tienen}")
    print(f"  SKIP (no en fund_master):  {no_en_master}")

    if dry_run:
        print(f"\n[DRY-RUN] No se escribió nada.")
        conn.close()
        return

    # ── Ejecutar ──────────────────────────────────────────────────────────
    if updates:
        conn.executemany(
            "UPDATE fund_kiid_metadata SET Raw_KIID_Text = ? WHERE ISIN = ?",
            updates,
        )
        print(f"\nUPDATE: {len(updates)} registros restaurados")

    if inserts:
        conn.executemany(
            """INSERT OR IGNORE INTO fund_kiid_metadata
               (ISIN, KIID_Class, KIID_URL, KIID_PDF_Hash, KIID_Status,
                Language, Raw_KIID_Text, KIID_Published_Date, KIID_Downloaded_At,
                SRRI, SRRI_Visual, SRRI_Textual, SRRI_Validation_Status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(r['ISIN'], r['KIID_Class'], r['KIID_URL'], r['KIID_PDF_Hash'],
              r['KIID_Status'], r['Language'], r['Raw_KIID_Text'],
              r['KIID_Published_Date'], r['KIID_Downloaded_At'],
              r['SRRI'], r['SRRI_Visual'], r['SRRI_Textual'],
              r['SRRI_Validation_Status']) for r in inserts],
        )
        print(f"INSERT: {len(inserts)} registros nuevos añadidos")

    conn.commit()

    # ── Verificar ─────────────────────────────────────────────────────────
    final = conn.execute(
        "SELECT COUNT(*), SUM(Raw_KIID_Text IS NOT NULL) FROM fund_kiid_metadata"
    ).fetchone()
    print(f"\nEstado final fund_kiid_metadata: {final[0]} registros, {final[1]} con texto")
    conn.close()
    print("Restauración completada.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restaura Raw_KIID_Text desde referencia Excel")
    parser.add_argument("--ref", default=None,
                        help="Ruta al Excel de referencia (default: p1_output_sqlite.xlsx)")
    parser.add_argument("--db", default=None,
                        help="Ruta a fondos.sqlite (default: db/fondos.sqlite)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Mostrar estadísticas sin escribir nada")
    args = parser.parse_args()

    ref_path = Path(args.ref) if args.ref else _ROOT / "p1_output_sqlite.xlsx"
    db_path  = Path(args.db)  if args.db  else _ROOT / "db" / "fondos.sqlite"

    if not ref_path.exists():
        print(f"ERROR: Referencia no encontrada: {ref_path}")
        sys.exit(1)
    if not db_path.exists():
        print(f"ERROR: BD no encontrada: {db_path}")
        sys.exit(1)

    restore_kiid_text(db_path, ref_path, dry_run=args.dry_run)
