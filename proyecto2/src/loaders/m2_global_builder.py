# proyecto2/src/calculations/m2_global_builder.py
# -*- coding: utf-8 -*-
"""
Construye la serie de M2 Global como suma ponderada de los M2
de USA, Eurozona, China y Japon, convertidos a USD.

M2_Global(t) = M2_US(t)
             + M2_EU(t)  * fx_usd_eur(t)   [EUR -> USD]
             + M2_CN(t)  / fx_cny_usd(t)   [CNY -> USD]  /1e6 (mn -> bn)
             + M2_JP(t)  / fx_jpy_usd(t)   [JPY -> USD]  /1e6 (mn -> bn)

Resultado almacenado en series_macro como:
    indicator='m2_global_yoy', geography='GLOBAL', unit='pct'

Tambien calcula la variacion interanual (YoY) que es el indicador
relevante para el modelo macro.
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT))


def build_m2_global(conn: sqlite3.Connection,
                    dry_run: bool = False) -> int:
    """
    Construye M2 Global y persiste en series_macro.
    Devuelve numero de registros escritos.
    """
    # Cargar M2 niveles
    q = """
        SELECT date, indicator, geography, value
        FROM series_macro
        WHERE (indicator='m2_level' AND geography IN ('US','EU','CN','JP'))
           OR (indicator='m2_yoy'   AND geography = 'EU')
           OR (indicator='fx_usd_eur' AND geography='GLOBAL')
           OR (indicator='fx_cny_usd' AND geography='GLOBAL')
           OR (indicator='fx_jpy_usd' AND geography='GLOBAL')
        ORDER BY date
    """
    rows = conn.execute(q).fetchall()
    if not rows:
        print("  [M2_Global] Sin datos")
        return 0

    df = pd.DataFrame(rows, columns=["date","indicator","geography","value"])
    df["date"]  = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["value"] = df["value"].astype(float)
    df["key"]   = df["indicator"] + "_" + df["geography"]

    wide = df.pivot_table(index="date", columns="key",
                          values="value", aggfunc="last")
    wide.columns.name = None

    # M2 EU: reconstruir nivel desde YoY si no tenemos nivel directo
    # Usamos m2_yoy_EU para calcular variacion pero necesitamos nivel
    # Alternativa: usar m3_index_EU como proxy del nivel EU (ya tenemos serie)
    # En su defecto, usar BCE m2_yoy para aproximar

    result = pd.DataFrame(index=wide.index)

    # M2 US: reconstruir nivel desde m2_yoy si no tenemos nivel directo
    if "m2_level_US" in wide.columns:
        result["m2_us"] = wide["m2_level_US"]
    elif "m2_yoy_US" in wide.columns:
        # M2SL en FRED contiene el nivel absoluto en bn USD (no el YoY)
        # aunque este cargado con indicador m2_yoy por error de naming
        # Valores tipicos: ~4600 en 2000, ~22000 en 2025
        vals = wide["m2_yoy_US"]
        if vals.median() > 1000:
            # Es nivel absoluto -- usar directamente
            result["m2_us"] = vals
        else:
            # Es YoY real -- reconstruir nivel con base 2010
            # Base: M2 US ~8800 bn USD en ene-2010
            base_date = "2010-01-31"
            m2_us_yoy = vals / 100
            level_us  = pd.Series(np.nan, index=m2_us_yoy.index)
            if base_date in [str(d)[:10] for d in level_us.index]:
                idx_base = [str(d)[:10] for d in level_us.index].index(base_date)
                level_us.iloc[idx_base] = 8800.0
                for i in range(idx_base + 1, len(level_us)):
                    yoy  = m2_us_yoy.iloc[i]
                    prev = level_us.iloc[i-1]
                    if not np.isnan(yoy) and not np.isnan(prev) and abs(yoy) < 1:
                        level_us.iloc[i] = prev * (1 + yoy/12)
                    else:
                        level_us.iloc[i] = prev
                for i in range(idx_base - 1, -1, -1):
                    yoy  = m2_us_yoy.iloc[i+1]
                    nxt  = level_us.iloc[i+1]
                    if not np.isnan(yoy) and not np.isnan(nxt) and abs(yoy) < 1:
                        level_us.iloc[i] = nxt / (1 + yoy/12)
                    else:
                        level_us.iloc[i] = nxt
            result["m2_us"] = level_us

    # M2 CN: MYAGM2CNM189N viene en CNY corrientes (no millones)
    # Solo llega hasta ago-2019 -- extender con YoY conocidos del PBoC
    if "m2_level_CN" in wide.columns and "fx_cny_usd_GLOBAL" in wide.columns:
        cn_level = wide["m2_level_CN"].copy()

        # YoY mensuales conocidos del PBoC para extender desde sep-2019
        # Fuente: PBoC press releases (valores aproximados mensuales)
        pboc_yoy = {
            "2019": 0.085, "2020": 0.100, "2021": 0.090,
            "2022": 0.115, "2023": 0.095, "2024": 0.085, "2025": 0.080,
        }

        last_valid = cn_level.last_valid_index()
        if last_valid is not None:
            all_dates = wide.index
            extend_dates = all_dates[all_dates > last_valid]
            for dt in extend_dates:
                yoy = pboc_yoy.get(str(dt.year), 0.085)
                prev = cn_level.get(dt - pd.DateOffset(months=1))
                if prev is not None and not np.isnan(prev):
                    cn_level[dt] = prev * (1 + yoy / 12)

        result["m2_cn_usd"] = (cn_level / wide["fx_cny_usd_GLOBAL"]) / 1_000_000_000

    # M2 JP: MYAGM2JPM189N viene en millones JPY
    # Convertir mn JPY -> USD bn: dividir por fx (JPY/USD) y por 1.000
    if "m2_level_JP" in wide.columns and "fx_jpy_usd_GLOBAL" in wide.columns:
        result["m2_jp_usd"] = (wide["m2_level_JP"] / wide["fx_jpy_usd_GLOBAL"]) / 1_000

    # M2 EU: usar nivel directo desde BCE (m2_level_EU en millones EUR)
    # Convertir millones EUR -> bn USD usando tipo de cambio
    if "m2_level_EU" in wide.columns and "fx_usd_eur_GLOBAL" in wide.columns:
        # m2_level_EU en millones EUR -> dividir por 1000 para bn EUR -> * fx para bn USD
        result["m2_eu_usd"] = (wide["m2_level_EU"] / 1000) * wide["fx_usd_eur_GLOBAL"]
    elif "m2_yoy_EU" in wide.columns and "fx_usd_eur_GLOBAL" in wide.columns:
        # Fallback: reconstruir desde YoY con base 2010
        base_eu_usd = 9000.0
        m2_eu_yoy = wide["m2_yoy_EU"] / 100
        level = pd.Series(np.nan, index=m2_eu_yoy.index)
        first = m2_eu_yoy.first_valid_index()
        if first:
            level[first] = base_eu_usd
            for i in range(level.index.get_loc(first)+1, len(level)):
                yoy = m2_eu_yoy.iloc[i]
                level.iloc[i] = level.iloc[i-1]*(1+yoy/12) if not np.isnan(yoy) else level.iloc[i-1]
        result["m2_eu_usd"] = level * wide["fx_usd_eur_GLOBAL"].reindex(level.index)


    # Construir M2 Global como suma de componentes disponibles
    m2_cols = [c for c in ["m2_us","m2_cn_usd","m2_jp_usd","m2_eu_usd"]
               if c in result.columns]

    if not m2_cols:
        print("  [M2_Global] Insuficientes componentes")
        return 0

    # Forward-fill componentes con datos parciales (CN llega hasta 2019, JP hasta 2017)
    # para evitar saltos bruscos en la suma cuando un componente desaparece
    result_ffill = result[m2_cols].ffill()

    # Exigir que US y EU esten siempre presentes (componentes principales)
    core_cols = [c for c in ["m2_us", "m2_eu_usd"] if c in result_ffill.columns]
    valid_mask = result_ffill[core_cols].notna().all(axis=1)

    result["m2_global"] = result_ffill[m2_cols].sum(axis=1, min_count=len(m2_cols))
    result.loc[~valid_mask, "m2_global"] = np.nan

    # YoY del M2 Global
    result["m2_global_yoy"] = result["m2_global"].pct_change(12, fill_method=None) * 100
    result = result.dropna(subset=["m2_global_yoy"])

    print(f"  [M2_Global] {len(result)} meses calculados | "
          f"componentes: {m2_cols}")
    print(f"  Rango: {result.index.min().strftime('%Y-%m')} - "
          f"{result.index.max().strftime('%Y-%m')}")
    print(f"  YoY: min={result['m2_global_yoy'].min():.1f}% "
          f"max={result['m2_global_yoy'].max():.1f}% "
          f"media={result['m2_global_yoy'].mean():.1f}%")

    if dry_run:
        return len(result)

    # Persistir
    conn.execute("""
        DELETE FROM series_macro
        WHERE indicator='m2_global_yoy' AND geography='GLOBAL'
    """)

    rows_out = []
    for date, row in result.iterrows():
        rows_out.append({
            "date":      date.strftime("%Y-%m-%d"),
            "indicator": "m2_global_yoy",
            "geography": "GLOBAL",
            "value":     round(float(row["m2_global_yoy"]), 4),
            "unit":      "pct",
            "source":    "CALC",
        })

    conn.executemany("""
        INSERT OR REPLACE INTO series_macro
            (date, indicator, geography, value, unit, source)
        VALUES (:date, :indicator, :geography, :value, :unit, :source)
    """, rows_out)
    conn.commit()

    print(f"  [M2_Global] {len(rows_out)} registros persistidos")
    return len(rows_out)


if __name__ == "__main__":
    import sys
    # Buscar BD en ubicaciones posibles
    candidates = [
        Path(_ROOT) / "db" / "fondos.sqlite",
        Path("db") / "fondos.sqlite",
        Path("fondos.sqlite"),
    ]
    db_path = next((p for p in candidates if p.exists()), None)
    if db_path is None:
        # Intentar desde config
        try:
            from shared.config import DB_PATH
            db_path = Path(DB_PATH)
        except Exception:
            pass
    if db_path is None or not db_path.exists():
        print(f"ERROR: No se encuentra fondos.sqlite. Probados: {[str(c) for c in candidates]}")
        sys.exit(1)
    print(f"BD: {db_path}")
    conn = sqlite3.connect(str(db_path))
    dry  = "--dry-run" in sys.argv
    n    = build_m2_global(conn, dry_run=dry)
    print(f"Total: {n} registros")
    conn.close()
