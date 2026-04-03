# prepare_upload.py
# -*- coding: utf-8 -*-
"""
Prepara el directorio de upload para actualizar el contexto del proyecto en Claude.

Estructura generada en c:/desarrollo/fondos/upload/:
    proyecto1upload/   -- copia de proyecto1 con ficheros renombrados *_upload.py
    proyecto2upload/   -- copia de proyecto2 con ficheros renombrados *_upload.py
    proyecto3upload/   -- copia de proyecto3 con ficheros renombrados *_upload.py
    tree/
        tree_proyectos.txt      -- arbol de proyecto1/2/3 originales
        tree_upload.txt         -- arbol de proyecto1/2/3upload
    code/
        *.py                    -- todos los ficheros python de los tres proyectos
                                   (con sufijo _upload para identificarlos)

Uso:
    cd c:/desarrollo/fondos
    python upload/prepare_upload.py
"""

import os
import shutil
from pathlib import Path
from datetime import datetime


# ============================================================
# Configuracion
# ============================================================

ROOT        = Path(__file__).resolve().parents[1]   # c:/desarrollo/fondos
UPLOAD_DIR  = ROOT / "upload"
PROJECTS    = ["proyecto1", "proyecto2", "proyecto3","shared"]


# ============================================================
# Utilidades
# ============================================================

def build_tree(base_path: Path, prefix: str = "") -> list[str]:
    """Genera representacion en arbol de directorios y ficheros .py."""
    lines = []
    try:
        entries = sorted(base_path.iterdir(), key=lambda x: (x.is_file(), x.name))
    except PermissionError:
        return lines

    entries = [e for e in entries
               if not e.name.startswith('.')
               and e.name != '__pycache__'
               and (e.is_dir() or e.suffix == '.py')]

    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.extend(build_tree(entry, prefix + extension))
    return lines


def collect_py_files(base_path: Path) -> list[Path]:
    """Recoge todos los ficheros .py de un directorio recursivamente."""
    return [p for p in base_path.rglob("*.py")
            if "__pycache__" not in str(p)]


# ============================================================
# Proceso principal
# ============================================================

def prepare_upload():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{'='*60}")
    print(f"  prepare_upload.py  |  {timestamp}")
    print(f"  Raiz: {ROOT}")
    print(f"{'='*60}")

    # Crear directorio upload si no existe
    UPLOAD_DIR.mkdir(exist_ok=True)

    # Directorios de salida
    tree_dir = UPLOAD_DIR / "tree"
    code_dir = UPLOAD_DIR / "code"
    tree_dir.mkdir(exist_ok=True)
    code_dir.mkdir(exist_ok=True)

    # Limpiar code/ para evitar ficheros obsoletos
    for f in code_dir.glob("*.py"):
        f.unlink()

    all_py_files  = []   # para el directorio code/
    tree_orig     = []   # arbol originales
    tree_upload   = []   # arbol uploads

    # --------------------------------------------------------
    # Procesar cada proyecto
    # --------------------------------------------------------
    for project in PROJECTS:
        src_dir    = ROOT / project
        upload_dir = UPLOAD_DIR / f"{project}upload"

        if not src_dir.exists():
            print(f"  [SKIP] {project} no encontrado en {src_dir}")
            continue

        print(f"\n  Procesando {project}...")

        # Limpiar directorio upload del proyecto
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        upload_dir.mkdir()

        py_files = collect_py_files(src_dir)
        print(f"    {len(py_files)} ficheros .py encontrados")

        for src_file in py_files:
            # Calcular ruta relativa respecto al proyecto
            rel_path = src_file.relative_to(src_dir)

            # Ruta destino en projectoXupload con sufijo _upload
            dest_stem = src_file.stem + "_upload"
            dest_name = dest_stem + ".py"
            dest_path = upload_dir / rel_path.parent / dest_name

            # Crear subdirectorios si no existen
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Copiar fichero
            shutil.copy2(src_file, dest_path)
            all_py_files.append(dest_path)

        print(f"    Copiados a {upload_dir.name}/")

        # Arbol originales
        tree_orig.append(f"\n{project}/")
        tree_orig.extend(build_tree(src_dir))

        # Arbol uploads
        tree_upload.append(f"\n{project}upload/")
        tree_upload.extend(build_tree(upload_dir))

    # --------------------------------------------------------
    # Copiar todos los .py al directorio code/
    # --------------------------------------------------------
    print(f"\n  Copiando {len(all_py_files)} ficheros a code/...")
    copied_code = 0
    name_conflicts = {}

    for src_file in all_py_files:
        dest = code_dir / src_file.name

        # Resolver conflictos de nombre (mismo nombre en proyectos distintos)
        if dest.exists():
            # Añadir prefijo con el proyecto
            project_prefix = src_file.parts[src_file.parts.index(
                next(p + "upload" for p in PROJECTS
                     if (p + "upload") in src_file.parts)
            )]
            dest = code_dir / f"{project_prefix}_{src_file.name}"

        shutil.copy2(src_file, dest)
        copied_code += 1

    print(f"    {copied_code} ficheros en code/")

    # --------------------------------------------------------
    # Generar ficheros de arbol
    # --------------------------------------------------------
    tree_orig_file = tree_dir / "tree_proyectos.txt"
    tree_orig_file.write_text(
        f"ARBOL DE DIRECTORIOS — PROYECTOS ORIGINALES\n"
        f"Generado: {timestamp}\n"
        f"Raiz: {ROOT}\n"
        + "\n".join(tree_orig),
        encoding="utf-8"
    )

    tree_upload_file = tree_dir / "tree_upload.txt"
    tree_upload_file.write_text(
        f"ARBOL DE DIRECTORIOS — PROYECTOS UPLOAD\n"
        f"Generado: {timestamp}\n"
        f"Raiz: {UPLOAD_DIR}\n"
        + "\n".join(tree_upload),
        encoding="utf-8"
    )

    print(f"\n  Arboles generados en tree/")

    # --------------------------------------------------------
    # Resumen final
    # --------------------------------------------------------
    total_py  = sum(len(collect_py_files(ROOT / p))
                    for p in PROJECTS if (ROOT / p).exists())
    total_up  = len(list((UPLOAD_DIR / "code").glob("*.py")))

    print(f"\n{'='*60}")
    print(f"  COMPLETADO")
    print(f"  Ficheros .py originales: {total_py}")
    print(f"  Ficheros en code/:       {total_up}")
    print(f"  Directorio upload:       {UPLOAD_DIR}")
    print(f"{'='*60}")
    print(f"\n  Para actualizar el contexto en Claude:")
    print(f"  Sube al proyecto el contenido completo de:")
    print(f"  {UPLOAD_DIR / 'code'}")
    print(f"  y los ficheros de:")
    print(f"  {UPLOAD_DIR / 'tree'}")


if __name__ == "__main__":
    prepare_upload()
