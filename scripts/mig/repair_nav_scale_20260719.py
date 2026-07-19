# scripts/mig/repair_nav_scale_20260719.py
# -*- coding: utf-8 -*-
"""
FIX-P2-NAV-SCALE-1 — Reparación puntual de picos NAV imposibles en fund_nav_monthly.

Problema observado (2026-07-19):
    El endpoint sal-service (Data_Source='MORNINGSTAR') devuelve en ocasiones el
    valor del índice de retorno total (acumulado desde inicio) en lugar del NAV de
    precio para fechas de cierre de calendario (último día del mes). Estos rows
    tienen NAV ~×100 respecto al período adyacente, por ejemplo:

      2016-01-29  MORNINGSTAR  87.92   ← NAV de precio correcto
      2016-01-31  MORNINGSTAR  9291.90  ← índice de retorno total acumulado (×106)
      2016-02-29  MORNINGSTAR  90.00

    Con estos datos, pct_change() produce un retorno de +10 464 % seguido de -99 %.
    srri.compute_srri() anualiza este ruido como vol_ann = 157–173, capped a srri=7,
    lo que contradice la naturaleza defensiva del fondo y genera NATURE_LOW_CONFIDENCE.

Solución (este script):
    Detecta y ELIMINA los rows-pico (NAV > 8× el row anterior Y > 8× el siguiente).
    No rescala — los rows-pico son puntos de datos cualitativamente distintos (índice
    acumulado ≠ precio), no simples errores de escala. Los meses afectados mantienen
    cobertura a través del row de precio adyacente (p.ej. el día 29 de enero).

    El script es IDEMPOTENTE: puede re-ejecutarse sin efecto secundario una vez que
    los rows-pico hayan sido eliminados (no quedan pares con ratio >8×).

Uso (ejecutar sobre copia de seguridad primero):
    # 1. Copia de seguridad
    copy db\\fondos.sqlite db\\fondos_backup_20260719.sqlite

    # 2. Verificación en seco
    python scripts\\mig\\repair_nav_scale_20260719.py --dry-run

    # 3. Ejecución real
    python scripts\\mig\\repair_nav_scale_20260719.py

    # 4. Verificación post-reparación
    python scripts\\mig\\repair_nav_scale_20260719.py --verify

Después de la reparación, re-ejecutar las métricas P2 para los ISINs afectados:
    python -X utf8 -m proyecto2.src.pipeline.run_pipeline --isin <ISIN>
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Configuración de paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent  # c:/desarrollo/fondos
_DB_PATH   = _REPO_ROOT / "db" / "fondos.sqlite"


# ---------------------------------------------------------------------------
# Detección de rows inflados — dos algoritmos complementarios
# ---------------------------------------------------------------------------

def _find_spike_rows(rows: list) -> List[Tuple[str, str, str]]:
    """Identifica rows con NAV-pico AISLADOS en una serie histórica (Pase 1).

    Un 'pico aislado' es un row r[i] tal que:
      - NAV[i] > 8 × NAV[i-1]  (salto hacia arriba desde el anterior)
      - NAV[i] > 8 × NAV[i+1]  (retorno al nivel normal en el siguiente)

    Para el último row de la serie (sin siguiente), se usa solo la condición
    NAV[-1] > 8 × NAV[-2].

    Devuelve lista de (ISIN, Date, Data_Source) de los rows a eliminar.

    NOTA: No detecta clusters de picos CONSECUTIVOS (p.ej. un tramo de varias
    observaciones seguidas a escala inflada). Para esos casos usar
    _find_cluster_rows().
    """
    if len(rows) < 3:
        return []

    sorted_rows = sorted(rows, key=lambda r: r["Date"])
    navs        = [r["NAV"] for r in sorted_rows]
    to_delete   = []

    # Interior: picos rodeados por valores normales en ambos lados
    for i in range(1, len(navs) - 1):
        n_prev = navs[i-1]
        n_curr = navs[i]
        n_next = navs[i+1]
        if n_prev > 0 and n_curr > 0 and n_next > 0:
            if n_curr > 8 * n_prev and n_curr > 8 * n_next:
                r = sorted_rows[i]
                to_delete.append((r["ISIN"], r["Date"], r["Data_Source"]))

    # Borde final: el último row es un pico si es > 8× el penúltimo
    if len(navs) >= 2:
        n_last = navs[-1]
        n_prev = navs[-2]
        if n_prev > 0 and n_last > 0 and n_last > 8 * n_prev:
            r = sorted_rows[-1]
            to_delete.append((r["ISIN"], r["Date"], r["Data_Source"]))

    return to_delete


def _find_cluster_rows(rows: list) -> List[Tuple[str, str, str]]:
    """Identifica rows inflados en CLUSTERS CONSECUTIVOS (Pase 2).

    Usa el percentil 25 de todos los NAVs de la serie como referencia de
    escala 'normal'. Cualquier row con NAV > 8 × p25 se considera inflado.

    Robusto hasta un 75 % de rows corruptos (p25 siempre cae en el rango
    limpio si al menos el 25 % de los rows son correctos).

    Complementa _find_spike_rows() para detectar:
    - Picos consecutivos (p.ej. un tramo de varios meses-cierre seguidos
      donde el endpoint transicionó a emitir solo el índice acumulado).
    - Picos al final de la serie no capturados por la detección de borde.

    Devuelve lista de (ISIN, Date, Data_Source) de los rows a eliminar.
    """
    if len(rows) < 4:
        return []

    import statistics as _st

    navs_sorted = sorted(r["NAV"] for r in rows if r.get("NAV", 0) > 0)
    if not navs_sorted:
        return []

    p25_idx   = max(0, len(navs_sorted) // 4 - 1)
    p25       = navs_sorted[p25_idx]
    threshold = 8 * p25

    if threshold <= 0:
        return []

    return [
        (r["ISIN"], r["Date"], r["Data_Source"])
        for r in rows
        if r.get("NAV", 0) > threshold
    ]


# ---------------------------------------------------------------------------
# Funciones auxiliares de BD
# ---------------------------------------------------------------------------

def _get_affected_isins(conn: sqlite3.Connection) -> List[str]:
    """ISINs con srri_volatility > 5 (firma de corrupción imposible)."""
    rows = conn.execute(
        "SELECT DISTINCT ISIN FROM fund_metrics "
        "WHERE metric='srri_volatility' AND value > 5",
    ).fetchall()
    return [r[0] for r in rows]


def _load_nav_rows(conn: sqlite3.Connection, isin: str) -> list:
    """Carga todas las filas fund_nav_monthly de un ISIN (ordenadas por Date)."""
    rows = conn.execute(
        "SELECT ISIN, Date, NAV, NAV_Currency, NAV_Type, Is_Estimated, Data_Source "
        "FROM fund_nav_monthly WHERE ISIN = ? ORDER BY Date",
        (isin,),
    ).fetchall()
    return [
        {
            "ISIN": r[0], "Date": r[1], "NAV": r[2],
            "NAV_Currency": r[3], "NAV_Type": r[4],
            "Is_Estimated": r[5], "Data_Source": r[6],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Verificación post-reparación
# ---------------------------------------------------------------------------

def run_verify(conn: sqlite3.Connection) -> None:
    """Verifica el estado post-reparación de la BD."""
    affected = _get_affected_isins(conn)
    if not affected:
        print("OK  Ningún ISIN con srri_volatility > 5 en fund_metrics.")
    else:
        print(f"WARN  Aún hay {len(affected)} ISINs con srri_volatility > 5:")
        for isin in affected[:20]:
            print(f"       {isin}")
        if len(affected) > 20:
            print(f"       ... ({len(affected) - 20} más)")
        print("  > Ejecutar P2 pipeline para los ISINs reparados y re-verificar.")

    # Verificar saltos >8× restantes en fund_nav_monthly
    jump_isins = conn.execute("""
        WITH nav_pairs AS (
            SELECT ISIN, Date,
                   NAV,
                   LAG(NAV) OVER (PARTITION BY ISIN ORDER BY Date) AS prev_nav
            FROM fund_nav_monthly
        )
        SELECT DISTINCT ISIN
        FROM nav_pairs
        WHERE prev_nav > 0
          AND (NAV / prev_nav > 8 OR NAV / prev_nav < 0.125)
    """).fetchall()
    if not jump_isins:
        print("OK  Ningún ISIN con saltos NAV >8× en fund_nav_monthly.")
    else:
        print(f"WARN  {len(jump_isins)} ISINs aún tienen saltos >8× en fund_nav_monthly:")
        for (isin,) in jump_isins[:10]:
            print(f"       {isin}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="FIX-P2-NAV-SCALE-1: elimina picos NAV imposibles de fund_nav_monthly."
    )
    parser.add_argument(
        "--db", default=str(_DB_PATH),
        help=f"Ruta a la base de datos SQLite (default: {_DB_PATH})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra las correcciones sin escribir en BD",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Solo verifica el estado post-reparación (no modifica nada)",
    )
    parser.add_argument(
        "--isin", default=None,
        help="Procesar solo este ISIN (útil para pruebas spot)",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: BD no encontrada en {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))

    if args.verify:
        run_verify(conn)
        conn.close()
        return

    # Seleccionar ISINs a procesar
    if args.isin:
        isins = [args.isin]
        print(f"Procesando ISIN individual: {args.isin}")
    else:
        isins = _get_affected_isins(conn)
        print(f"ISINs con srri_volatility > 5 (firma de corrupción): {len(isins)}")

    if not isins:
        print("Nada que reparar — la BD ya está limpia.")
        conn.close()
        return

    total_deleted_rows = 0
    total_corrected_isins = 0

    for isin in isins:
        rows = _load_nav_rows(conn, isin)
        if not rows:
            continue

        # Pase 1: picos aislados (interior + borde)
        spike_keys = _find_spike_rows(rows)

        # Pase 2: clusters de picos consecutivos no capturados por Pase 1
        # (sobre el estado ACTUAL del lote, que ya incluye lo del Pase 1 si no es dry-run)
        remaining_rows = [r for r in rows
                          if (r["ISIN"], r["Date"], r["Data_Source"]) not in set(spike_keys)]
        cluster_keys = _find_cluster_rows(remaining_rows)

        all_keys = list(dict.fromkeys(spike_keys + cluster_keys))  # dedup, orden
        if not all_keys:
            continue

        total_corrected_isins += 1
        total_deleted_rows    += len(all_keys)

        action = "[DRY-RUN]" if args.dry_run else "[DELETE]"
        print(f"  {action} {isin}: {len(all_keys)} rows-pico "
              f"(P1={len(spike_keys)}, P2={len(cluster_keys)}, total={len(rows)})")
        if args.dry_run:
            for isin_key, date, src in all_keys[:5]:
                nav_val = next(
                    r["NAV"] for r in rows
                    if r["Date"] == date and r["Data_Source"] == src
                )
                print(f"            {date}  {src}  NAV={nav_val:.2f}")
            if len(all_keys) > 5:
                print(f"            ... ({len(all_keys) - 5} más)")

        if not args.dry_run:
            for isin_key, date, src in all_keys:
                conn.execute(
                    "DELETE FROM fund_nav_monthly "
                    "WHERE ISIN=? AND Date=? AND Data_Source=?",
                    (isin_key, date, src),
                )

    if not args.dry_run and total_deleted_rows > 0:
        conn.commit()

    label = "se eliminarían" if args.dry_run else "eliminadas"
    print(
        f"\nResumen: {total_corrected_isins} ISINs procesados, "
        f"{total_deleted_rows} filas NAV-pico {label}."
    )
    if args.dry_run:
        print("(Ejecutar sin --dry-run para aplicar los cambios.)")
    else:
        print(
            "OK BD actualizada. Recuerda ejecutar el pipeline P2 para los ISINs "
            "afectados para recalcular srri_nav y demás métricas."
        )

    conn.close()


if __name__ == "__main__":
    main()
