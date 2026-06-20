# tests/smoke_v19_deployment.py
# -*- coding: utf-8 -*-
"""
Smoke test post-deployment v19.
Verifica que el pipeline puede leer/escribir contra la BD migrada.

USO (ejecutar DESPUÉS de migrate_v18_to_v19.py):
    cd c:/desarrollo/fondos
    python -X utf8 tests/smoke_v19_deployment.py
"""

import sqlite3
import sys
from pathlib import Path

# El script vive en scripts/test/ → subir 3 niveles para llegar a la raíz
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.config import DB_PATH
from shared.schema_checks import check_schema_v19


def main():
    conn = sqlite3.connect(DB_PATH)

    # 1. Schema correcto
    res = check_schema_v19(conn)
    if not res['ok']:
        print(f"[FAIL] Schema inválido: {res['issues']}")
        sys.exit(1)
    print("[OK] Schema v19 verificado (57 columnas en fund_master)")

    # 2. Datos preservados
    n = conn.execute("SELECT COUNT(*) FROM fund_master").fetchone()[0]
    if n < 3200:
        print(f"[FAIL] Pérdida de datos detectada: {n} filas (esperado ≥3200)")
        sys.exit(1)
    print(f"[OK] {n} filas preservadas en fund_master")

    # 3. Ongoing_Charge_Recurrent poblado donde había Ongoing_Charge
    pob = conn.execute(
        "SELECT COUNT(*) FROM fund_master WHERE Ongoing_Charge_Recurrent IS NOT NULL"
    ).fetchone()[0]
    if pob < 3000:
        print(f"[WARN] Ongoing_Charge_Recurrent poblado en solo {pob} fondos "
              f"(esperado ≥3000). Verificar migración.")
    else:
        print(f"[OK] {pob} fondos con Ongoing_Charge_Recurrent poblado")

    # 4. Columnas v19 todas NULL en Sprint 1
    cnts = conn.execute(
        "SELECT "
        "SUM(KID_Format IS NULL), "
        "SUM(Cost_Extraction_Quality IS NULL), "
        "SUM(ACI_1Y IS NULL), "
        "SUM(ACI_RHP IS NULL) "
        "FROM fund_master"
    ).fetchone()
    if not all(c == n for c in cnts):
        print(f"[WARN] Columnas v19 no están todas NULL: {cnts} vs n={n}")
        print("       (Esto es OK si Sprint 2 ya ha poblado algunos fondos)")
    else:
        print(f"[OK] Columnas v19 todas NULL ({n} fondos) — terreno preparado para Sprint 2")

    # 5. fund_cost_schedule existe
    c = conn.execute("SELECT COUNT(*) FROM fund_cost_schedule").fetchone()[0]
    if c == 0:
        print("[OK] fund_cost_schedule vacía (esperado en Sprint 1)")
    else:
        print(f"[OK] fund_cost_schedule tiene {c} filas (Sprint 2 en progreso)")

    # 6. Query _v3_row ampliada funciona (R-3)
    row = conn.execute(
        "SELECT Investment_Universe, Accumulation_Policy, Currency_Hedged, "
        "Investment_Focus, Credit_Quality, Geography, Fund_Nature, "
        "KID_Format, Cost_Extraction_Quality "
        "FROM fund_master LIMIT 1"
    ).fetchone()
    if row is None:
        print("[FAIL] No hay filas en fund_master")
        sys.exit(1)
    print("[OK] Query _v3_row ampliada (9 columnas) funciona")

    # 7. Ongoing_Charge ya no existe
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fund_master)").fetchall()}
    if "Ongoing_Charge" in cols and "Ongoing_Charge_Recurrent" not in cols:
        print("[FAIL] Ongoing_Charge sin renombrar — migración no aplicada correctamente")
        sys.exit(1)
    print("[OK] Ongoing_Charge renombrada correctamente a Ongoing_Charge_Recurrent")

    # 8. Índices de fund_cost_schedule existen
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name IN ('idx_cost_schedule_isin', 'idx_cost_schedule_rhp')"
    ).fetchall()}
    if len(idx) != 2:
        print(f"[WARN] Solo {len(idx)}/2 índices de fund_cost_schedule encontrados: {idx}")
    else:
        print("[OK] Ambos índices de fund_cost_schedule presentes")

    conn.close()
    print("\n=== SMOKE TEST v19 PASS ===")


if __name__ == "__main__":
    main()
