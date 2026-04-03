# proyecto3/src/regime_classifier.py
# -*- coding: utf-8 -*-
"""
Clasificador de regimen macroeconomico.

Determina en que regimen macroeconomico nos encontramos en cada momento
usando los indicadores disponibles en series_macro. Opera en dos modos:

  1. Tiempo real: clasifica el regimen del ultimo mes disponible
  2. Historico: clasifica cada mes desde 2000 para analisis retrospectivo

Regimenes definidos (prioridad descendente en el arbol de decision):
  Crisis_Financiera    Override: spread HY > 600bps Y VIX YoY > 30% (estres sistemico)
  Shock_Energetico     Override: precio petroleo +25% interanual (cualquier entorno)
  Estanflacion         Crecimiento debil/negativo con inflacion alta
  Contraccion          Recesion con inflacion baja o deflacion
  Recalentamiento_Tardio  Tipos subiendo, inflacion alta, ciclo aun positivo
  Recalentamiento      Crecimiento fuerte, inflacion alta y subiendo
  Expansion            Crecimiento normal, tipos moderados, inflacion baja

Pesos de sub-carteras por regimen:
  (Defensiva%, Equilibrada%, Dinamica%)
  Crisis_Financiera:      70 / 25 /  5
  Shock_Energetico:       55 / 35 / 10
  Estanflacion:           50 / 35 / 15
  Contraccion:            60 / 35 /  5
  Recalentamiento_Tardio: 40 / 40 / 20
  Recalentamiento:        30 / 40 / 30
  Expansion:              20 / 45 / 35

Uso:
    from proyecto3.src.regime_classifier import RegimeClassifier
    clf = RegimeClassifier(conn)
    regimen_actual = clf.classify_current()
    historia = clf.classify_historical()
"""

import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
import sys
from dataclasses import dataclass

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


# ============================================================
# Constantes
# ============================================================

REGIMES = [
    "Crisis_Financiera",
    "Shock_Energetico",
    "Estanflacion",
    "Contraccion",
    "Recalentamiento_Tardio",
    "Recalentamiento",
    "Expansion",
]

# Pesos de sub-carteras por regimen (Defensiva, Equilibrada, Dinamica)
REGIME_WEIGHTS = {
    "Crisis_Financiera":     (0.70, 0.25, 0.05),
    "Shock_Energetico":      (0.55, 0.35, 0.10),
    "Estanflacion":          (0.50, 0.35, 0.15),
    "Contraccion":           (0.60, 0.35, 0.05),
    "Recalentamiento_Tardio":(0.40, 0.40, 0.20),
    "Recalentamiento":       (0.30, 0.40, 0.30),
    "Expansion":             (0.20, 0.45, 0.35),
}

# Umbrales de clasificacion
OIL_SHOCK_THRESHOLD    = 0.25   # variacion interanual WTI > 25% -> shock energetico
IPC_HIGH_THRESHOLD     = 0.04   # inflacion > 4% -> alta
IPC_MOD_THRESHOLD      = 0.03   # inflacion > 3% -> moderada-alta
CLI_EXPANSION          = 100.0  # CLI > 100 -> expansion
RATE_LOW_THRESHOLD     = 0.01   # tipo deposito < 1% -> tipos bajos

# Umbrales Crisis_Financiera
# spread_hy en % (BAMLH0A0HYM2): media historica ~400bps, crisis 2008 >1900bps
# vix_yoy en %: cambio interanual del VIX medio mensual
SPREAD_HY_CRISIS       = 6.0    # spread > 600bps = estres credito severo
VIX_YOY_CRISIS         = 30.0   # VIX YoY > +30% = miedo sistematico elevado

# Umbrales del semaforo de regimen
SEMAFORO_OIL_MOM       = 0.08   # petroleo sube >8% mensual -> senal amber
SEMAFORO_IPC_ACCEL     = 0.003  # IPC acelera >0.3pp/mes -> senal amber
SEMAFORO_CLI_DROP      = 2.0    # CLI cae >2 puntos en 1 mes -> senal rojo
SEMAFORO_CLI_CROSS     = 1.0    # CLI cruza 100 +/- 1 punto -> senal amber
SEMAFORO_RATE_JUMP     = 0.50   # tipo sube/baja >0.5pp en 1 mes -> senal rojo
SEMAFORO_OIL_SPIKE     = 0.20   # petroleo sube >20% en 1 mes -> senal rojo
SEMAFORO_SPREAD_AMBER  = 4.0    # spread_hy > 400bps -> senal amber
SEMAFORO_SPREAD_RED    = 6.0    # spread_hy > 600bps -> senal rojo
SEMAFORO_VIX_MOM       = 0.15   # VIX sube >15% mensual -> senal amber
SEMAFORO_VIX_SPIKE     = 0.30   # VIX sube >30% mensual -> senal rojo


@dataclass
class RegimeResult:
    """Resultado de la clasificacion de un periodo."""
    date:              pd.Timestamp
    regime:            str
    weight_defensive:  float
    weight_balanced:   float
    weight_dynamic:    float
    # Indicadores de ciclo economico
    oil_yoy:           float | None
    ipc_yoy_avg:       float | None
    cli_eu:            float | None
    rate_deposit:      float | None
    d_rate_3m:         float | None
    # Indicadores de estres financiero (nuevos)
    spread_hy:         float | None
    vix_yoy:           float | None

    @property
    def weights(self) -> dict:
        return {
            "Defensiva":   self.weight_defensive,
            "Equilibrada": self.weight_balanced,
            "Dinamica":    self.weight_dynamic,
        }

    def __str__(self) -> str:
        return (f"{self.date.strftime('%Y-%m')} | {self.regime:25s} | "
                f"D:{self.weight_defensive:.0%} "
                f"E:{self.weight_balanced:.0%} "
                f"Di:{self.weight_dynamic:.0%}")


@dataclass
class SemaforoResult:
    """Resultado del semaforo de regimen."""
    color:       str
    signals:     list[str]
    regime_prev: str | None
    regime_curr: str

    def __str__(self) -> str:
        signal_str = " | ".join(self.signals) if self.signals else "Sin senales"
        return f"Semaforo: {self.color} | {signal_str}"


# ============================================================
# Carga de datos macro
# ============================================================

def _load_macro_series(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    Carga y pivota todas las series macro necesarias para la clasificacion.
    Devuelve DataFrame mensual con columnas:
        oil_wti, ipc_yoy_es, ipc_yoy_eu, cli_eu, rate_deposit, m3_yoy
    Fechas normalizadas a fin de mes.
    """
    query = """
        SELECT date, indicator, geography, value
        FROM series_macro
        WHERE (indicator = 'oil_wti'      AND geography = 'GLOBAL')
           OR (indicator = 'copper'       AND geography = 'GLOBAL')
           OR (indicator = 'ipc_index'    AND geography IN ('ES','EU','US','JP','CN'))
           OR (indicator = 'cli'          AND geography IN ('EU','US','JP','CN','ES'))
           OR (indicator = 'rate_deposit' AND geography = 'EU')
           OR (indicator = 'rate_policy'  AND geography IN ('US','JP','CN'))
           OR (indicator = 'm3_yoy'       AND geography = 'EU')
           OR (indicator = 'm2_yoy'       AND geography = 'US')
           OR (indicator = 'unemployment' AND geography = 'US')
           OR (indicator = 'dxy'          AND geography = 'GLOBAL')
           OR (indicator = 'gold'         AND geography = 'GLOBAL')
           OR (indicator = 'm2_global_yoy' AND geography = 'GLOBAL')
           OR (indicator = 'spread_hy'    AND geography = 'GLOBAL')
           OR (indicator = 'vix'          AND geography = 'GLOBAL')
        ORDER BY date
    """
    rows = conn.execute(query).fetchall()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["date", "indicator", "geography", "value"])
    df["date"]  = pd.to_datetime(df["date"]) + pd.offsets.MonthEnd(0)
    df["value"] = df["value"].astype(float)
    df["key"]   = df["indicator"] + "_" + df["geography"]

    wide = df.pivot_table(index="date", columns="key",
                          values="value", aggfunc="last")
    wide.columns.name = None

    # Variacion interanual WTI
    if "oil_wti_GLOBAL" in wide.columns:
        wide["oil_yoy"] = wide["oil_wti_GLOBAL"].pct_change(12, fill_method=None)

    # IPC variacion interanual desde indice base 100
    for col, new_col in [("ipc_index_ES", "ipc_yoy_es"),
                          ("ipc_index_EU", "ipc_yoy_eu")]:
        if col in wide.columns:
            wide[new_col] = wide[col].pct_change(12, fill_method=None)

    # Media IPC ES y EU
    ipc_cols = [c for c in ["ipc_yoy_es", "ipc_yoy_eu"] if c in wide.columns]
    if ipc_cols:
        wide["ipc_yoy_avg"] = wide[ipc_cols].mean(axis=1)

    # CLI EU (nivel)
    if "cli_EU" in wide.columns:
        wide["cli_eu"] = wide["cli_EU"]

    # Variacion tipos 3 meses
    if "rate_deposit_EU" in wide.columns:
        wide["rate_deposit"] = wide["rate_deposit_EU"]
        wide["d_rate_3m"]    = wide["rate_deposit_EU"].diff(3)

    # M3 y M2
    if "m3_yoy_EU" in wide.columns:
        wide["m3_yoy"] = wide["m3_yoy_EU"]
    if "m2_yoy_US" in wide.columns:
        wide["m2_yoy_us"] = wide["m2_yoy_US"]

    # Tipos US, JP, CN y su variacion mensual
    for col, new_col in [
        ("rate_policy_US", "rate_us"),
        ("rate_policy_JP", "rate_jp"),
        ("rate_policy_CN", "rate_cn"),
    ]:
        if col in wide.columns:
            wide[new_col] = wide[col]
            wide[f"d_{new_col}"] = wide[col].diff()

    # Cobre variacion interanual
    if "copper_GLOBAL" in wide.columns:
        wide["copper_yoy"] = wide["copper_GLOBAL"].pct_change(12, fill_method=None) * 100

    # CLI adicionales
    for geo in ["US", "JP", "CN", "ES"]:
        col = f"cli_{geo}"
        if col in wide.columns:
            wide[f"cli_{geo.lower()}"] = wide[col]

    # IPC adicionales
    for geo, col_src in [("us","ipc_index_US"),("jp","ipc_index_JP"),("cn","ipc_index_CN")]:
        if col_src in wide.columns:
            wide[f"ipc_yoy_{geo}"] = wide[col_src].pct_change(12, fill_method=None) * 100

    # Desempleo US
    if "unemployment_US" in wide.columns:
        wide["unemployment_us"] = wide["unemployment_US"]

    # DXY, Oro y M2 Global
    if "dxy_GLOBAL" in wide.columns:
        wide["dxy_yoy"] = wide["dxy_GLOBAL"].pct_change(12, fill_method=None) * 100
    if "gold_GLOBAL" in wide.columns:
        wide["gold_yoy"] = wide["gold_GLOBAL"].pct_change(12, fill_method=None) * 100
    if "m2_global_yoy_GLOBAL" in wide.columns:
        wide["m2_global_yoy"] = wide["m2_global_yoy_GLOBAL"]

    # Spread HY y VIX -- indicadores de estres financiero sistemico
    # spread_hy: nivel directo en % (600bps = 6.0)
    # vix_yoy: variacion interanual del VIX medio mensual
    if "spread_hy_GLOBAL" in wide.columns:
        wide["spread_hy"] = wide["spread_hy_GLOBAL"]
    if "vix_GLOBAL" in wide.columns:
        wide["vix_yoy"] = wide["vix_GLOBAL"].pct_change(12, fill_method=None) * 100

    cols = ["oil_yoy", "copper_yoy",
            "ipc_yoy_avg", "ipc_yoy_es", "ipc_yoy_eu", "ipc_yoy_us",
            "ipc_yoy_jp", "ipc_yoy_cn",
            "cli_eu", "cli_us", "cli_jp", "cli_cn", "cli_es",
            "rate_deposit", "d_rate_3m",
            "rate_us", "rate_jp", "rate_cn",
            "m3_yoy", "m2_yoy_us", "unemployment_us",
            "dxy_yoy", "gold_yoy", "m2_global_yoy",
            "spread_hy", "vix_yoy"]
    available = [c for c in cols if c in wide.columns]
    return wide[available]


# ============================================================
# Logica de clasificacion
# ============================================================

def _classify_row(
    oil_yoy:      float | None,
    ipc_yoy_avg:  float | None,
    cli_eu:       float | None,
    rate_deposit: float | None,
    d_rate_3m:    float | None,
    spread_hy:    float | None = None,
    vix_yoy:      float | None = None,
) -> str:
    """
    Arbol de decision para clasificar un mes.

    Prioridad:
    1. Crisis financiera (override si spread_hy > 600bps Y vix_yoy > 30%)
    2. Shock energetico (override si oil_yoy > 25%)
    3. Contraccion / Estanflacion (segun CLI y IPC)
    4. Recalentamiento / Recalentamiento_Tardio
    5. Expansion (caso base)
    """
    # -- Override: crisis financiera --------------------------
    # Requiere AMBAS condiciones para evitar falsos positivos:
    # spread_hy elevado puede darse en recesion normal;
    # vix_yoy elevado puede darse en correcciones breves.
    # La combinacion de ambos senala estres sistemico real.
    if (spread_hy is not None and not np.isnan(spread_hy) and
            vix_yoy is not None and not np.isnan(vix_yoy)):
        if spread_hy > SPREAD_HY_CRISIS and vix_yoy > VIX_YOY_CRISIS:
            return "Crisis_Financiera"

    # -- Override: shock energetico ---------------------------
    if oil_yoy is not None and not np.isnan(oil_yoy):
        if oil_yoy > OIL_SHOCK_THRESHOLD:
            return "Shock_Energetico"

    # -- Ciclo debil (CLI < 100) ------------------------------
    if cli_eu is not None and not np.isnan(cli_eu):
        if cli_eu < CLI_EXPANSION:
            if ipc_yoy_avg is not None and not np.isnan(ipc_yoy_avg):
                if ipc_yoy_avg > IPC_MOD_THRESHOLD:
                    return "Estanflacion"
            return "Contraccion"

    # -- Ciclo positivo (CLI >= 100) --------------------------
    if ipc_yoy_avg is not None and not np.isnan(ipc_yoy_avg):
        if ipc_yoy_avg > IPC_HIGH_THRESHOLD:
            # Tipos subiendo -> recalentamiento tardio
            if d_rate_3m is not None and not np.isnan(d_rate_3m) and d_rate_3m > 0:
                return "Recalentamiento_Tardio"
            return "Recalentamiento"

    # -- Tipos muy bajos -> desinflacion ----------------------
    if rate_deposit is not None and not np.isnan(rate_deposit):
        if rate_deposit < RATE_LOW_THRESHOLD:
            return "Contraccion"  # tipos bajos con ciclo ok = desinflacion benigna

    return "Expansion"


# ============================================================
# Clase principal
# ============================================================

class RegimeClassifier:
    """
    Clasificador de regimen macroeconomico.

    Uso:
        clf = RegimeClassifier(conn)
        actual  = clf.classify_current()
        historia = clf.classify_historical()
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn   = conn
        self._macro = _load_macro_series(conn)

    def classify_current(self) -> RegimeResult:
        """Clasifica el regimen del ultimo mes disponible."""
        if self._macro.empty:
            raise ValueError("No hay datos macro disponibles en series_macro.")

        # Forward-fill para propagar ultimo dato disponible de cada indicador
        # (los distintos indicadores tienen fechas fin distintas)
        filled = self._macro.ffill()
        last   = filled.iloc[-1]
        date   = last.name

        def _get(col):
            return float(last[col]) if col in last.index and not pd.isna(last[col]) else None

        oil_yoy      = _get("oil_yoy")
        ipc_yoy_avg  = _get("ipc_yoy_avg")
        cli_eu       = _get("cli_eu")
        rate_deposit = _get("rate_deposit")
        d_rate_3m    = _get("d_rate_3m")
        spread_hy    = _get("spread_hy")
        vix_yoy      = _get("vix_yoy")

        regime  = _classify_row(oil_yoy, ipc_yoy_avg, cli_eu,
                                 rate_deposit, d_rate_3m, spread_hy, vix_yoy)
        weights = REGIME_WEIGHTS[regime]

        return RegimeResult(
            date=date, regime=regime,
            weight_defensive=weights[0],
            weight_balanced=weights[1],
            weight_dynamic=weights[2],
            oil_yoy=oil_yoy,
            ipc_yoy_avg=ipc_yoy_avg,
            cli_eu=cli_eu,
            rate_deposit=rate_deposit,
            d_rate_3m=d_rate_3m,
            spread_hy=spread_hy,
            vix_yoy=vix_yoy,
        )

    def classify_historical(self) -> pd.DataFrame:
        """
        Clasifica cada mes historico disponible.

        Devuelve DataFrame con columnas:
            date, regime, weight_defensive, weight_balanced, weight_dynamic,
            oil_yoy, ipc_yoy_avg, cli_eu, rate_deposit, d_rate_3m
        """
        if self._macro.empty:
            return pd.DataFrame()

        # Forward-fill para propagar ultimo dato disponible mes a mes
        macro_filled = self._macro.ffill()

        records = []
        for date, row in macro_filled.iterrows():
            def _get(col):
                v = row.get(col)
                return float(v) if v is not None and not pd.isna(v) else None

            oil_yoy      = _get("oil_yoy")
            ipc_yoy_avg  = _get("ipc_yoy_avg")
            cli_eu       = _get("cli_eu")
            rate_deposit = _get("rate_deposit")
            d_rate_3m    = _get("d_rate_3m")
            spread_hy    = _get("spread_hy")
            vix_yoy      = _get("vix_yoy")

            # Solo clasificar si tenemos al menos CLI e IPC
            if ipc_yoy_avg is None and cli_eu is None:
                continue

            regime  = _classify_row(oil_yoy, ipc_yoy_avg, cli_eu,
                                     rate_deposit, d_rate_3m, spread_hy, vix_yoy)
            weights = REGIME_WEIGHTS[regime]

            records.append({
                "date":             date,
                "regime":           regime,
                "weight_defensive": weights[0],
                "weight_balanced":  weights[1],
                "weight_dynamic":   weights[2],
                "oil_yoy":          oil_yoy,
                "ipc_yoy_avg":      ipc_yoy_avg,
                "cli_eu":           cli_eu,
                "rate_deposit":     rate_deposit,
                "d_rate_3m":        d_rate_3m,
                "spread_hy":        spread_hy,
                "vix_yoy":          vix_yoy,
            })

        return pd.DataFrame(records).set_index("date")

    def regime_summary(self) -> pd.DataFrame:
        """Distribucion historica de regimenes (% del tiempo en cada uno)."""
        hist = self.classify_historical()
        if hist.empty:
            return pd.DataFrame()
        counts = hist["regime"].value_counts()
        pct    = (counts / counts.sum() * 100).round(1)
        return pd.DataFrame({"meses": counts, "pct": pct})

    def semaforo(self, n_last: int = 3) -> SemaforoResult:
        """
        Semaforo de estabilidad del regimen actual.
        Verde: estable | Ambar: senales de cambio | Rojo: cambio confirmado
        """
        hist   = self.classify_historical()
        filled = self._macro.ffill()
        signals = []
        color   = "Verde"

        # Cambio de regimen reciente
        if len(hist) >= 2:
            curr = hist["regime"].iloc[-1]
            prev = hist["regime"].iloc[-2]
            if curr != prev:
                signals.append(f"Cambio regimen: {prev} -> {curr}")
                color = "Rojo"
        else:
            curr = hist["regime"].iloc[-1] if not hist.empty else "Desconocido"
            prev = None

        # Shock petroleo mensual
        if "oil_wti_GLOBAL" in filled.columns:
            oil = filled["oil_wti_GLOBAL"].dropna().iloc[-2:]
            if len(oil) == 2 and oil.iloc[0] > 0:
                oil_mom = (oil.iloc[-1] - oil.iloc[0]) / oil.iloc[0]
                if oil_mom > SEMAFORO_OIL_SPIKE:
                    signals.append(f"Shock petroleo: +{oil_mom*100:.1f}% mensual")
                    color = "Rojo"
                elif oil_mom > SEMAFORO_OIL_MOM:
                    signals.append(f"Petroleo acelerando: +{oil_mom*100:.1f}%/mes")
                    if color == "Verde":
                        color = "Ambar"

        # CLI cruzando 100 o cayendo bruscamente
        if "cli_eu" in filled.columns:
            cli = filled["cli_eu"].dropna().iloc[-2:]
            if len(cli) == 2:
                cli_drop = cli.iloc[-2] - cli.iloc[-1]
                if cli_drop > SEMAFORO_CLI_DROP:
                    signals.append(f"CLI cae {cli_drop:.1f}pts en 1 mes")
                    color = "Rojo"
                elif abs(cli.iloc[-1] - CLI_EXPANSION) < SEMAFORO_CLI_CROSS:
                    signals.append(f"CLI en zona critica: {cli.iloc[-1]:.1f}")
                    if color == "Verde":
                        color = "Ambar"

        # IPC acelerando
        if "ipc_yoy_avg" in filled.columns:
            ipc = filled["ipc_yoy_avg"].dropna().iloc[-2:]
            if len(ipc) == 2:
                ipc_accel = ipc.iloc[-1] - ipc.iloc[0]
                if ipc_accel > SEMAFORO_IPC_ACCEL * 2:
                    signals.append(f"IPC acelera {ipc_accel*100:.2f}pp")
                    if color == "Verde":
                        color = "Ambar"

        # Tipos con salto brusco
        if "rate_deposit" in filled.columns:
            rate = filled["rate_deposit"].dropna().iloc[-2:]
            if len(rate) == 2:
                rate_jump = abs(rate.iloc[-1] - rate.iloc[0])
                if rate_jump >= SEMAFORO_RATE_JUMP:
                    signals.append(f"Tipos saltan {rate_jump:.2f}pp en 1 mes")
                    color = "Rojo"

        # Spread HY -- estres de credito
        if "spread_hy" in filled.columns:
            spread = filled["spread_hy"].dropna()
            if not spread.empty:
                s = spread.iloc[-1]
                if s > SEMAFORO_SPREAD_RED:
                    signals.append(f"Spread HY critico: {s:.1f}%")
                    color = "Rojo"
                elif s > SEMAFORO_SPREAD_AMBER:
                    signals.append(f"Spread HY elevado: {s:.1f}%")
                    if color == "Verde":
                        color = "Ambar"

        # VIX -- miedo de mercado
        if "vix_GLOBAL" in filled.columns:
            vix = filled["vix_GLOBAL"].dropna().iloc[-2:]
            if len(vix) == 2 and vix.iloc[0] > 0:
                vix_mom = (vix.iloc[-1] - vix.iloc[0]) / vix.iloc[0]
                if vix_mom > SEMAFORO_VIX_SPIKE:
                    signals.append(f"VIX spike: +{vix_mom*100:.1f}% mensual")
                    color = "Rojo"
                elif vix_mom > SEMAFORO_VIX_MOM:
                    signals.append(f"VIX subiendo: +{vix_mom*100:.1f}%/mes")
                    if color == "Verde":
                        color = "Ambar"

        return SemaforoResult(
            color=color,
            signals=signals,
            regime_prev=prev if prev != curr else None,
            regime_curr=curr,
        )

    def current_regime_report(self) -> str:
        """Informe legible del regimen actual."""
        r = self.classify_current()
        lines = [
            f"REGIMEN MACRO ACTUAL ({r.date.strftime('%B %Y')})",
            f"{'='*45}",
            f"Regimen:          {r.regime}",
            f"",
            f"Indicadores de ciclo:",
            f"  IPC medio EU+ES: {r.ipc_yoy_avg*100:.1f}%" if r.ipc_yoy_avg else "  IPC: N/D",
            f"  CLI Eurozona:    {r.cli_eu:.1f}" if r.cli_eu else "  CLI: N/D",
            f"  Tipo deposito:   {r.rate_deposit:.2f}%" if r.rate_deposit else "  Tipo: N/D",
            f"  Petroleo YoY:    {r.oil_yoy*100:+.1f}%" if r.oil_yoy else "  Petroleo: N/D",
            f"",
            f"Indicadores de estres financiero:",
            f"  Spread HY:       {r.spread_hy:.2f}%" if r.spread_hy else "  Spread HY: N/D",
            f"  VIX YoY:         {r.vix_yoy:+.1f}%" if r.vix_yoy else "  VIX YoY: N/D",
            f"",
            f"Pesos de cartera recomendados:",
            f"  Defensiva:       {r.weight_defensive:.0%}",
            f"  Equilibrada:     {r.weight_balanced:.0%}",
            f"  Dinamica:        {r.weight_dynamic:.0%}",
        ]
        sem = self.semaforo()
        lines += [
            f"",
            f"Semaforo:         {sem.color}",
        ]
        for s in sem.signals:
            lines.append(f"  ! {s}")
        return "\n".join(lines)
