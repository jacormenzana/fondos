# init_dbv1.py
# -*- coding: utf-8 -*-
"""
FICHERO OBSOLETO — conservado solo como referencia histórica.

Este fichero fue la versión inicial de inicialización de la base de datos
de Proyecto 2, cuando aún usaba SQLAlchemy y una BD separada (funds.db).

Ha sido sustituido por:
    init_db.py  ← versión actual, usa sqlite3 puro y fondos.sqlite unificado

No ejecutar directamente. Si necesitas reinicializar la base de datos:
    python init_db.py
    python init_db.py --check-only
    python init_db.py --force-recreate   (⚠ destructivo, hace backup)
"""

raise RuntimeError(
    "init_dbv1.py está obsoleto. Usa init_db.py en su lugar.\n"
    "  python init_db.py"
)
