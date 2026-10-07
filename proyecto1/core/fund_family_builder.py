# proyecto1/core/fund_family_builder.py
# -*- coding: utf-8 -*-
"""
Asignacion de fund_family_id en fund_master.  — post-BL-59

Cambios post-BL-59 (2026-04-29):
  BL-59  Caso límite: Restantes mayoritario + única Nature concreta.
         Causa raíz: en _resolve_family_nature, cuando Restantes tiene
         mayoría simple (ej. 3/5 = 60% < 66.7%), la Regla 2 no aplica.
         En Regla 3, srri_without_restantes contiene solo la Nature
         concreta (ej. 'Renta Fija Corto Plazo'). others_srri y others_dq
         resultan vacíos, srri_diff=0, dq_diff=0 → el if final nunca
         se cumple → return None, [] (no corrige).
         Fix: tras los pasos SRRI/DQ de la Regla 3, si
         len(srri_without_restantes)==1, esa Nature concreta gana
         incondicionalmente. Restantes es por definición el clasificador
         fallback; no puede prevalecer sobre una clasificación positiva.
         Test: FAM_000261 (BGF China Bond: 3 Restantes + 2 RFCP) → Nature
         debe resolverse a 'Renta Fija Corto Plazo', 3 ISINs corregidos.
         Condición de guarda añadida: solo se activa si nature_set contiene
         'Restantes' (evita falsos positivos en familias sin Restantes).

Agrupa clases de acciones del mismo fondo bajo un identificador comun
normalizando el nombre del fondo y eliminando sufijos de clase.

Problema:
    Un fondo como "Fundsmith Equity Fund T EUR Acc" y
    "Fundsmith Equity Fund T USD Acc" son clases del mismo fondo subyacente.
    El pipeline los trata como fondos independientes.
    fund_family_id permite:
      - Deduplicacion correcta en portfolio_builder (max 2 por familia)
      - Analisis de costes comparados entre clases
      - Scoring consolidado por fondo real

Metodologia de agrupacion:
    1. Normalizar nombre: minusculas, eliminar acentos, colapsar espacios
    2. Eliminar sufijos de clase conocidos (divisas, letras, distribucion,
       cobertura) mediante expresion regular iterativa
    3. Agrupar por (Management_Company, nombre_normalizado)
    4. Asignar IDs secuenciales: FAM_000001, FAM_000002, ...
    5. Actualizar fund_master.fund_family_id en batch

Uso:
    cd c:/desarrollo/fondos
    python proyecto1/core/fund_family_builder.py

    # O desde Python:
    from proyecto1.core.fund_family_builder import build_fund_families
    n = build_fund_families(conn)
"""

import re
import unicodedata
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

try:
    from proyecto1.core.classify_utils import (  # BL-64e DRY (P#11); FND-0244: the family arbitration reuses the one classifier
        RFC_INCOMPATIBLE_FAMILIES, detect_nature_from_kiid, resolve_nature_evidence,
    )
    from proyecto1.core.benchmark_normalizer import merge_benchmark_sources
except ImportError:
    from core.classify_utils import RFC_INCOMPATIBLE_FAMILIES, detect_nature_from_kiid, resolve_nature_evidence
    from core.benchmark_normalizer import merge_benchmark_sources

from shared.db import executemany, execute_fail_soft, fail_soft_block


# Familias con heterogeneidad de Fund_Nature CONOCIDA, identificadas por su NOMBRE NORMALIZADO (la raiz que produce _normalize_name), nunca
# por fund_family_id: el builder REASIGNA los FAM_xxxxxx secuencialmente en cada ejecucion (sorted(groups)), asi que una lista por id
# apuntaba a otras familias en cuanto cambiaba el universo (FND-0244, 2026-10-07: de los 4 ids documentados, 3 ya apuntaban a otro fondo y
# ocultaban inconsistencias REALES). Solo se suprimen del AVISO de post-correccion las familias de esta lista.
#   allianz best styles at: LU2696130686 EUR = Mixtos, LU2710823126 USD = Renta Variable. Misma evidencia KIID/Morningstar; la clase USD
#   cruza a RV por el arbitraje de volatilidad realizada (banda 6 frente a 5, en parte por el NAV en USD). Es un desempate, no diseno del
#   gestor: se resolvera con el arbitraje a nivel de familia de FND-0244. Retiradas (ya homogeneas): ashmore sicav em sd, capital g nw persp
#   bd, dws float rate note.
_KNOWN_HETEROGENEOUS_STEMS: frozenset[str] = frozenset({
    "allianz best styles at",
})


# ============================================================
# Sufijos de clase a eliminar (orden importa: del mas especifico al general)
# ============================================================

# Cada patron se aplica al final del nombre normalizado (sin acentos, lower)
# hasta que no haya mas cambios (bucle hasta convergencia)
_CLASS_SUFFIXES = re.compile(
    r"""
    \s+(
        # -- Cobertura divisa --
        h(?:edged?)?              # H, Hgd, Hedge, Hedged
        | eur\s*h(?:edged?)?      # EUR H, EUR Hedged
        | usd\s*h(?:edged?)?      # USD H, USD Hedged
        | \(h\)                   # (H)
        # FND-0244 (2026-10-07): the catalogue names are cut at ~30 characters and abbreviate the hedge marker. Measured on the 3,762
        # names: "hdg"/"hed" (48 + 13 funds), the currency glued to the marker for EVERY currency ("EURH", "GBPH", "USDHDG", "CHFH"),
        # and the Capital Group-style class codes that end in H ("BH", "ZH", "PH", "AH", "BDH", "BGDH", "ZDH", "PDH").
        | hdg | hed
        | (?:eur|usd|gbp|chf|jpy|sek|nok|dkk|aud|cad|sgd|hkd|cnh)\s*h(?:dg|ed)?
        | [abpz]g?d?h

        # -- Divisas ISO (solas al final) --
        | eur | usd | gbp | jpy | chf | aud | cad | sek | nok | dkk
        | hkd | sgd | cny | cnh | pln | czk | huf | mxn | brl | inr

        # -- Tipo de participacion / distribucion --
        | acc(?:umulation)?       # Acc, Accumulation
        | ac                      # FND-0244: "Acc" cut by the 30-character name limit -- 725 of 3,762 funds (19%) ended in it and, because
                                  # it was not a suffix, blocked every suffix before it: each formed a single-fund family
        | in                      # FND-0244: "Inc" cut by the same limit ("... EUR IN"); never a class-less word at the END of a fund name
        | dist(?:ribution)?       # Dist, Distribution
        | inc                     # Inc abreviado únicamente.
                                  # "Income" (palabra completa) NO se trata
                                  # como sufijo de clase — es ambiguo: puede
                                  # ser parte del nombre del fondo (Templeton
                                  # Global Income, GS Eq Income). Trade-off:
                                  # clases hermanas que solo difieran en el
                                  # sufijo "Income" quedan en familias
                                  # separadas. Falso negativo aceptable;
                                  # falso positivo destruye granularidad
                                  # (BL-FAM-FIX D4, 26-abr-2026).
        | cap(?:ital(?:isation)?)?# Cap, Capital, Capitalisation
        | thes(?:aurisation)?     # Thes (FR)
        | dis(?:trib)?            # Dis

        # -- Letras de clase (solas o con numero) --
        | [a-z]\d*                # A, B, C, ... Z, A1, B2, ...
        | \d+                     # solo numero al final

        # -- Tipo de inversor --
        | retail | institutional | inst | instl
        | clean | dirty
        | r | i | p | e | x | z   # letras sueltas comunes de clase

        # -- Codigos de clase propios de gestora (FND-0244, medidos: parte de los fondos que acaban en el codigo comparten raiz
        #    con un hermano de la misma gestora en el 54-92% de los casos, n >= 5) --
        | lc | ld | fc | fd | nc | sc | tfc | tfd | lch

        # -- Otros sufijos comunes --
        | nr | net | gross
        | lux | irl | ie | lv     # domicilio
        | ucits | etf
        | eur\s*class | usd\s*class
    )$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """
    Normaliza un nombre de fondo para comparacion entre clases.

    Pasos:
    1. Eliminar acentos (NFD -> ASCII)
    2. Minusculas
    3. Eliminar caracteres no alfanumericos excepto espacios
    4. Eliminar sufijos de clase (hasta convergencia)
    5. Colapsar espacios
    """
    if not name:
        return ""

    # Eliminar acentos
    nfkd = unicodedata.normalize("NFD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))

    # Minusculas y limpiar
    s = ascii_name.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Eliminar sufijos iterativamente hasta convergencia
    for _ in range(10):   # max 10 iteraciones para evitar bucle infinito
        s_prev = s
        s = _strip_one_class_suffix(s)
        if s == s_prev:
            break

    return s.strip()


def _strip_one_class_suffix(s: str) -> str:
    """Strip ONE trailing class suffix. FND-0244 acronym guard: a run of THREE OR MORE single letters ("S D E M D A", "N G C F I") is the
    fund's own acronym spelled with spaces, not a stack of class letters; stripping it collapsed two different Neuberger funds into the
    stem "neuberger". Two spaced letters ("F N", "T A", "A C": Franklin / JPM class codes) and letter+digit codes ("A2", "D4") are class
    codes and keep being stripped, as before."""
    m = _CLASS_SUFFIXES.search(s)
    if not m:
        return s
    token = m.group(1)
    if re.fullmatch(r"[a-z]", token):
        run = 1
        for t in reversed(s[: m.start()].split()):
            if not re.fullmatch(r"[a-z]", t):
                break
            run += 1
        if run >= 3:
            return s
    return s[: m.start()].strip()


# ============================================================
# Constructor principal
# ============================================================


# ============================================================
# CORRECCIÓN ESCALABLE DE INCONSISTENCIAS DE NATURALEZA
# ============================================================

# Señales que indican heterogeneidad INTENCIONAL — no son errores de clasificación
_STRUCTURAL_HETEROGENEITY_SIGNALS = [
    "hedge", "hedged", "long short", "long/short",
    "absolute return", "abs ret", "market neutral",
    "short ", " short",
]

# Pares de naturalezas "adyacentes" — el error puede estar en cualquiera
_ADJACENT_NATURE_PAIRS = {
    frozenset({"Mixtos", "Renta Variable"}),
    frozenset({"Mixtos", "Renta Fija Flexible"}),
    frozenset({"Mixtos", "Renta Fija Corto Plazo"}),        # BL-FAM-FIX D1
    frozenset({"Renta Fija Flexible", "Renta Fija Corto Plazo"}),
    frozenset({"Monetario", "Renta Fija Corto Plazo"}),
    frozenset({"Alternativo", "Renta Variable"}),
    frozenset({"Monetario", "Renta Variable"}),
}

# Naturalezas que son adyacentes a CUALQUIER otra Nature (BL-FAM-FIX D1).
# Restantes es el fallback del clasificador: si una familia tiene miembros
# en Restantes junto a cualquier Nature concreta, la inconsistencia es
# error del bloque clasificador, no heterogeneidad real del fondo.
_UNIVERSAL_ADJACENT: frozenset = frozenset({"Restantes"})


def _is_adjacent_pair(nature_set: set) -> bool:
    """
    Devuelve True si el conjunto de naturalezas es 'adyacente'.

    Una naturaleza en _UNIVERSAL_ADJACENT (Restantes) es adyacente
    a cualquier otra. Para el resto, consulta _ADJACENT_NATURE_PAIRS.
    """
    if nature_set & _UNIVERSAL_ADJACENT:
        return True
    return any(frozenset(nature_set) == p for p in _ADJACENT_NATURE_PAIRS)

# Jerarquía de confianza para SRRI_Quality_Flag
_SRRI_QUALITY_RANK = {
    "HIGH": 4, "MEDIUM_TEXT": 3, "MEDIUM_VISUAL": 2,
    "LOW_CONFLICT": 1, "NONE": 0, None: 0,
}


def _is_structural_heterogeneity(names: list[str]) -> bool:
    """
    Devuelve True si la heterogeneidad es intencional —
    al menos una clase tiene señal estructural (hedge, L/S, AR).
    """
    for name in names:
        name_l = (name or "").lower()
        if any(sig in name_l for sig in _STRUCTURAL_HETEROGENEITY_SIGNALS):
            return True
    return False


def _resolve_family_nature(
    members: list[dict],
) -> tuple[str | None, list[str]]:
    """
    Determina la naturaleza correcta para una familia inconsistente.

    Devuelve:
        (naturaleza_correcta, [ISINs a corregir])
        o (None, []) si no se puede determinar de forma segura.

    Reglas (por orden de precedencia):
    1. Heterogeneidad estructural → no corregir
    2. Mayoría ≥ 2/3 + discordantes con Data_Quality=MISSING/WARN → aplicar mayoría
    2-bis. Familias bipartitas: jerarquía Restantes > DQ > SRRI_Quality (BL-FAM-FIX D2)
    3. Naturalezas adyacentes: SRRI_Quality primario + DQ desempate (BL-FAM-FIX D3)
    """
    names = [m["Fund_Name"] for m in members]
    natures = [m["Fund_Nature"] for m in members]
    nature_set = set(n for n in natures if n)

    # Regla 1: heterogeneidad estructural — mantener
    if _is_structural_heterogeneity(names):
        return None, []

    if len(nature_set) <= 1:
        return None, []  # ya es consistente

    # Regla 2: mayoría ≥ 2/3 + discordantes con calidad baja
    from collections import Counter
    counts = Counter(natures)
    majority_nature, majority_count = counts.most_common(1)[0]
    total = len(members)

    if majority_count / total >= 2/3:
        # Identificar discordantes
        discordant = [
            m for m in members
            if m["Fund_Nature"] != majority_nature
        ]
        # Solo corregir si los discordantes tienen calidad baja
        low_quality_discordant = [
            m for m in discordant
            if m.get("Data_Quality_Flag") in ("MISSING", "WARN")
            or m.get("SRRI_Quality_Flag") in ("NONE", None)
        ]
        if len(low_quality_discordant) == len(discordant):
            # Todos los discordantes tienen calidad baja → aplicar mayoría
            return majority_nature, [m["ISIN"] for m in discordant]

    # === REGLA 2-bis: Familias bipartitas (BL-FAM-FIX D2) ===
    # Con total=2, mayoría=1, ratio=0.5 < 0.667 → la Regla 2 clásica
    # nunca aplica. Esta regla resuelve familias de exactamente 2 miembros
    # con naturalezas distintas mediante jerarquía de calidad.
    if len(members) == 2 and len(nature_set) == 2:
        m_a, m_b = members[0], members[1]

        # 2-bis-A: uno es Restantes → el otro gana siempre
        if m_a["Fund_Nature"] == "Restantes" and m_b["Fund_Nature"] != "Restantes":
            return m_b["Fund_Nature"], [m_a["ISIN"]]
        if m_b["Fund_Nature"] == "Restantes" and m_a["Fund_Nature"] != "Restantes":
            return m_a["Fund_Nature"], [m_b["ISIN"]]

        # 2-bis-B: asimetría en Data_Quality_Flag
        dq_a = m_a.get("Data_Quality_Flag")
        dq_b = m_b.get("Data_Quality_Flag")
        if dq_a == "OK" and dq_b in ("MISSING", "WARN"):
            return m_a["Fund_Nature"], [m_b["ISIN"]]
        if dq_b == "OK" and dq_a in ("MISSING", "WARN"):
            return m_b["Fund_Nature"], [m_a["ISIN"]]

        # 2-bis-C: asimetría clara en SRRI_Quality_Flag
        sq_a = _SRRI_QUALITY_RANK.get(m_a.get("SRRI_Quality_Flag"), 0)
        sq_b = _SRRI_QUALITY_RANK.get(m_b.get("SRRI_Quality_Flag"), 0)
        if sq_a >= 2 and sq_b == 0:
            return m_a["Fund_Nature"], [m_b["ISIN"]]
        if sq_b >= 2 and sq_a == 0:
            return m_b["Fund_Nature"], [m_a["ISIN"]]

        # 2-bis-D: calidades equivalentes → cae a Regla 3 (adyacencia)

    # Regla 3: naturalezas adyacentes → calidad SRRI primaria + DQ desempate
    # (BL-FAM-FIX D3: umbral anterior >2 era demasiado restrictivo y no
    # contemplaba desempate por Data_Quality_Flag)
    if _is_adjacent_pair(nature_set):
        srri_by_nature: dict = {}
        dq_by_nature: dict = {}
        for m in members:
            nat = m["Fund_Nature"]
            srri_by_nature[nat] = srri_by_nature.get(nat, 0) + \
                _SRRI_QUALITY_RANK.get(m.get("SRRI_Quality_Flag"), 0)
            dq_rank = {"OK": 2, "WARN": 1, "MISSING": 0}.get(
                m.get("Data_Quality_Flag"), 0
            )
            dq_by_nature[nat] = dq_by_nature.get(nat, 0) + dq_rank

        if srri_by_nature:
            # Restantes no puede ganar como Nature destino — es fallback del
            # clasificador. Si resulta ser el mejor por SRRI agregado (porque
            # tiene más miembros), excluirlo y elegir el siguiente.
            srri_without_restantes = {k: v for k, v in srri_by_nature.items()
                                      if k != "Restantes"}
            if not srri_without_restantes:
                return None, []  # todos son Restantes — consistente

            best_nature = max(srri_without_restantes, key=srri_without_restantes.get)
            best_srri = srri_without_restantes[best_nature]
            others_srri = {k: v for k, v in srri_by_nature.items()
                           if k != best_nature}
            srri_diff = best_srri - max(others_srri.values()) if others_srri else 0

            if srri_diff > 2:
                # SRRI claramente superior → aplicar directamente
                to_correct = [m["ISIN"] for m in members
                              if m["Fund_Nature"] != best_nature]
                return best_nature, to_correct

            if srri_diff >= 0:
                # SRRI igual o ligeramente superior → desempatar por DQ
                best_dq_nature = max(dq_by_nature, key=dq_by_nature.get)
                best_dq = dq_by_nature[best_dq_nature]
                others_dq = {k: v for k, v in dq_by_nature.items()
                             if k != best_dq_nature}
                dq_diff = best_dq - max(others_dq.values()) if others_dq else 0

                # DQ diferencia ≥ 1 y coincide con el ganador SRRI
                if dq_diff >= 1 and best_dq_nature == best_nature:
                    to_correct = [m["ISIN"] for m in members
                                  if m["Fund_Nature"] != best_nature]
                    return best_nature, to_correct

            # BL-59: caso límite — Restantes mayoritario pero existe exactamente
            # UNA Nature concreta no-Restantes. others_srri/others_dq quedan
            # vacíos porque best_nature es la única Nature no-Restantes y
            # others_srri = {Restantes: N} → best_nature se excluye de others_srri
            # → el máximo de others_srri es el SRRI de Restantes, pero al calcular
            # dq_diff, others_dq = {Restantes: M} y best_dq_nature puede ser
            # Restantes → el if (best_dq_nature == best_nature) nunca se cumple.
            # Fix: si después de los pasos anteriores hay exactamente una Nature
            # concreta (srri_without_restantes tiene 1 entrada), esa Nature gana
            # incondicionalmente — Restantes es por definición el clasificador
            # fallback y no puede prevalecer sobre una clasificación concreta.
            # Guarda: solo cuando Restantes está presente en nature_set (evita
            # activarse en familias con dos Natures concretas distintas).
            if len(srri_without_restantes) == 1 and "Restantes" in nature_set:
                to_correct = [m["ISIN"] for m in members
                              if m["Fund_Nature"] != best_nature]
                return best_nature, to_correct

    return None, []  # No determinable de forma segura


# ============================================================
# FND-0244 part 2 -- family-level nature arbitration (rule 4)
# ============================================================
#
# The share classes of ONE fund hold the same portfolio, so they have ONE nature. They end up with different ones for reasons unrelated to
# the fund: each class is classified alone, from its own KIID (classes of one fund come in different languages - the phrase lists are mostly
# Spanish - or with gaps), its own benchmark / Morningstar row (hedged classes often have none) and its own realized-volatility band, which
# moves with the NAV currency (Allianz Best Styles AT: USD class band 6 -> Renta Variable by the volatility veto, EUR class band 5 -> Mixtos,
# identical KIID / benchmark / Morningstar evidence). Rules 1-3 only look at quality flags and cannot decide these.
#
# Rule 4 decides the family ONCE with the single classifier (resolve_nature_evidence, P#11): the REFERENCE class is the one a EUR investor
# holds (fund_currency EUR first - the platform's investor-currency semantics, FND-0243), then the one whose KIID yields an ex-ante vote,
# then the longest NAV history, then the lowest ISIN; its name, KIID text and realized-volatility band are used, and the benchmark /
# Morningstar signals are completed from any sibling that has them. Applied only to ADJACENT natures (never a RV vs Monetario merge, which
# would point at a false family) and never to Restantes; the winner must be one of the natures the family already has.

def _reference_sort_key(m: dict) -> tuple:
    has_vote = detect_nature_from_kiid(m.get("kiid_text") or "") is not None
    return (
        (m.get("fund_currency") or "").upper() != "EUR",
        not has_vote,
        -(m.get("nav_n") or 0),
        m["ISIN"],
    )


def resolve_family_nature_by_reference(members: list[dict]) -> tuple[str | None, list[str], str]:
    """Rule 4. `members` carry ISIN, Fund_Name, Fund_Nature, fund_currency, nav_n, kiid_text, benchmark_declared, ms_asset_class, srri_band.

    Returns (nature, [ISINs to correct], reason); (None, [], reason) when the family cannot be decided safely."""
    nature_set = {m["Fund_Nature"] for m in members if m.get("Fund_Nature")}
    if len(nature_set) <= 1:
        return None, [], "consistent"
    if "Restantes" in nature_set or not _is_adjacent_pair(nature_set):
        return None, [], "non-adjacent natures: left for review"
    ref = sorted(members, key=_reference_sort_key)[0]
    if not (ref.get("kiid_text") or "").strip():
        return None, [], "reference class has no KIID text"
    bench = next((m["benchmark_declared"] for m in sorted(members, key=_reference_sort_key) if m.get("benchmark_declared")), None)
    ms = next((m["ms_asset_class"] for m in sorted(members, key=_reference_sort_key) if m.get("ms_asset_class")), None)
    nature, conf, trace = resolve_nature_evidence((ref["Fund_Name"] or "").lower(), ref["kiid_text"], bench, ref.get("srri_band"), ms)
    if nature is None or nature not in nature_set:
        return None, [], f"reference class {ref['ISIN']} resolves to {nature!r}, not one of the family's natures"
    return nature, [m["ISIN"] for m in members if m["Fund_Nature"] != nature],         f"reference class {ref['ISIN']} ({trace.get('reason')}, conf {conf:.2f})"


def _load_family_evidence(conn, members: list[dict]) -> list[dict]:
    """Add the inputs resolve_nature_evidence needs to each member of an inconsistent family (a handful of families per run)."""
    isins = [m["ISIN"] for m in members]
    ph = ",".join("%s" for _ in isins)
    kiid = dict(conn.execute(
        f"SELECT ISIN, Raw_KIID_Text FROM fund_kiid_metadata WHERE KIID_Class = 1 AND ISIN IN ({ph})", isins).fetchall())
    meta = {r[0]: r[1:] for r in conn.execute(
        f"SELECT ISIN, Fund_Currency, Benchmark_Declared FROM fund_master WHERE ISIN IN ({ph})", isins).fetchall()}
    nav = dict(conn.execute(f"SELECT ISIN, COUNT(*) FROM fund_nav_monthly WHERE ISIN IN ({ph}) GROUP BY ISIN", isins).fetchall())
    bmk = merge_benchmark_sources(conn.execute(
        f"SELECT ISIN, source, asset_class, benchmark_role, benchmark_name, confidence FROM fund_benchmarks "
        f"WHERE source IN ('MORNINGSTAR','KIID') AND ISIN IN ({ph})", isins).fetchall())
    band = {r[0]: int(round(float(r[1]))) for r in conn.execute(
        f"SELECT ISIN, value FROM fund_metrics WHERE metric = 'srri_nav' AND horizon = 'since_inception' AND real_flag = 0 "
        f"AND value IS NOT NULL AND ISIN IN ({ph})", isins).fetchall()}
    out = []
    for m in members:
        i = m["ISIN"]
        out.append({**m, "kiid_text": kiid.get(i), "fund_currency": (meta.get(i) or (None, None))[0],
                    "benchmark_declared": (meta.get(i) or (None, None))[1], "nav_n": nav.get(i, 0),
                    "ms_asset_class": (bmk.get(i) or {}).get("asset_class"), "srri_band": band.get(i)})
    return out


def correct_family_inconsistencies(
    conn: "psycopg.Connection",
    dry_run: bool = False,
) -> int:
    """
    Detecta y corrige inconsistencias de Fund_Nature dentro de familias.
    Opera exclusivamente sobre atributos escalables — sin referencias a nombres específicos.

    Devuelve número de correcciones aplicadas.
    """
    # Obtener datos necesarios para la evaluación
    rows = conn.execute("""
        SELECT fm.ISIN, fm.Fund_Name, fm.Fund_Nature, fm.fund_family_id,
               fm.Data_Quality_Flag, fm.SRRI_Quality_Flag
        FROM fund_master fm
        WHERE fm.fund_family_id IS NOT NULL
          AND fm.Fund_Nature IS NOT NULL
        ORDER BY fm.fund_family_id
    """).fetchall()

    from collections import defaultdict
    families: dict = defaultdict(list)
    for isin, name, nature, fam_id, dq, sq in rows:
        families[fam_id].append({
            "ISIN": isin,
            "Fund_Name": name,
            "Fund_Nature": nature,
            "Data_Quality_Flag": dq,
            "SRRI_Quality_Flag": sq,
        })

    corrections = []
    skipped_structural = 0
    skipped_uncertain = 0

    for fam_id, members in families.items():
        natures = set(m["Fund_Nature"] for m in members)
        if len(natures) <= 1:
            continue  # familia consistente

        correct_nature, isins_to_fix = _resolve_family_nature(members)
        if not correct_nature and not _is_structural_heterogeneity([m["Fund_Name"] for m in members]):
            # FND-0244 part 2: rules 1-3 look only at quality flags; rule 4 decides by the reference class's evidence
            try:
                with fail_soft_block(conn):
                    correct_nature, isins_to_fix, _why = resolve_family_nature_by_reference(_load_family_evidence(conn, members))
            except Exception as _exc:
                correct_nature, isins_to_fix = None, []
                print(f"  [FamilyBuilder] rule 4 skipped for {fam_id}: {type(_exc).__name__}")

        if correct_nature and isins_to_fix:
            for isin in isins_to_fix:
                old_nature = next(m["Fund_Nature"] for m in members if m["ISIN"] == isin)
                corrections.append((correct_nature, isin, fam_id, old_nature))
        elif _is_structural_heterogeneity([m["Fund_Name"] for m in members]):
            skipped_structural += 1
        else:
            skipped_uncertain += 1

    print(f"  [FamilyBuilder] Inconsistencias encontradas: "
          f"{len(corrections) + skipped_structural + skipped_uncertain}")
    print(f"  [FamilyBuilder]   Corregibles (regla escalable): {len(corrections)}")
    print(f"  [FamilyBuilder]   Heterogeneidad estructural:    {skipped_structural}")
    print(f"  [FamilyBuilder]   No determinables:              {skipped_uncertain}")

    if dry_run or not corrections:
        if corrections:
            print("  [FamilyBuilder] DRY-RUN — correcciones que se aplicarían:")
            for nat, isin, fam, old in corrections[:10]:
                name = next((m["Fund_Name"] for f_id, mems in families.items()
                             for m in mems if m["ISIN"] == isin), isin)
                print(f"    {fam} {isin} {name[:35]} {old} → {nat}")
        return len(corrections)

    # Aplicar correcciones
    ph = "%s"
    _now_sql = "now()"
    executemany(
        conn,
        f"UPDATE fund_master SET Fund_Nature = {ph} WHERE ISIN = {ph}",
        [(nat, isin) for nat, isin, _, _ in corrections],
    )

    # FIX-BL64E-FAMCORR (2026-07-04): re-aplicar BL-64e tras revertir Nature a
    # RFC. Causa raíz: BL-44 (pipeline.py) marca correctamente Fund_Nature=
    # 'Restantes' para fondos RFC con SRRI incompatible, y BL-64e (misma pasada
    # por-fondo) corrige Family solo cuando Fund_Nature=='Renta Fija Corto
    # Plazo' -- pero en ese momento Nature ya es 'Restantes', así que BL-64e
    # nunca dispara. Family queda en el valor lexicalmente re-inferido por
    # BL-62 (p.ej. 'Emerging Market Debt'). Más tarde, ESTA función (regla 3
    # de _resolve_family_nature, que excluye 'Restantes' como Nature destino
    # por diseño) revierte Nature de vuelta a 'Renta Fija Corto Plazo' -- pero
    # nunca vuelve a comprobar Family, dejando la combinación RFC+Family
    # incompatible persistida indefinidamente (confirmado: 3 fondos BGF China
    # Bond). RFC_INCOMPATIBLE_FAMILIES importado de classify_utils (P#11 DRY).
    _family_fix_isins = [
        isin for nat, isin, _, _ in corrections
        if nat == "Renta Fija Corto Plazo"
    ]
    if _family_fix_isins:
        _placeholders = ",".join(ph for _ in _family_fix_isins)
        _fam_rows = conn.execute(
            f"SELECT ISIN, Family FROM fund_master WHERE ISIN IN ({_placeholders})",
            _family_fix_isins,
        ).fetchall()
        _to_fix = [isin for isin, fam in _fam_rows if fam in RFC_INCOMPATIBLE_FAMILIES]
        if _to_fix:
            executemany(
                conn,
                f"UPDATE fund_master SET Family = 'Short-Term Fixed Income' WHERE ISIN = {ph}",
                [(isin,) for isin in _to_fix],
            )
            for isin in _to_fix:
                execute_fail_soft(
                    conn,
                    f"""INSERT INTO ingestion_log
                       (ISIN, Step, Status, Message, Created_At)
                       VALUES ({ph}, 'BL64E_FAMCORR_REAPPLIED', 'INFO', {ph}, {_now_sql})""",
                    (isin, "Family corregido a 'Short-Term Fixed Income' tras "
                           "reversión de Nature Restantes→RFC (BL-64e re-aplicado)")
                )
            print(f"  [FamilyBuilder] BL-64e re-aplicado tras corrección de familia: "
                  f"{len(_to_fix)} fondos")

    # Registrar en ingestion_log
    for nat, isin, fam_id, old_nature in corrections:
        execute_fail_soft(
            conn,
            f"""INSERT INTO ingestion_log
               (ISIN, Step, Status, Message, Created_At)
               VALUES ({ph}, 'FAMILY_NATURE_CORRECTION', 'INFO', {ph}, {_now_sql})""",
            (isin, f"Fund_Nature corregido: {old_nature} → {nat} "
                   f"(familia {fam_id}, regla escalable)")
        )

    conn.commit()
    print(f"  [FamilyBuilder] {len(corrections)} correcciones aplicadas")
    return len(corrections)


def build_fund_families(
    conn: "psycopg.Connection",
    dry_run: bool = False,
) -> int:
    """
    Asigna fund_family_id a todos los fondos en fund_master.

    Logica:
    - Fondos con mismo (Management_Company, nombre_normalizado) -> misma familia
    - IDs asignados en orden de aparicion: FAM_000001, FAM_000002, ...
    - Fondos ya con fund_family_id se respetan (no se sobreescriben)
      a menos que --force se active (no implementado en v1)

    Devuelve numero de filas actualizadas.
    """
    rows = conn.execute(
        "SELECT ISIN, Fund_Name, Management_Company FROM fund_master "
        "WHERE Fund_Name IS NOT NULL ORDER BY Management_Company, Fund_Name"
    ).fetchall()

    if not rows:
        print("  [FamilyBuilder] Sin fondos en fund_master")
        return 0

    # Agrupar por (gestora, nombre_normalizado)
    groups: dict[tuple, list[str]] = {}
    for isin, name, company in rows:
        company_key = (company or "").strip().lower()
        norm        = _normalize_name(name)
        key         = (company_key, norm)
        groups.setdefault(key, []).append(isin)

    # Asignar IDs secuenciales
    # Solo crea familia si hay >1 ISIN en el grupo (singleton no necesita ID)
    # Los singletons reciben ID propio para que portfolio_builder pueda
    # usar fund_family_id universalmente
    updates: list[tuple[str, str]] = []   # (family_id, isin)
    family_data: list[tuple] = []         # (family_id, display_name, n_funds)
    family_counter = 1

    for (company, norm_name), isins in sorted(groups.items()):
        fam_id = f"FAM_{family_counter:06d}"
        family_counter += 1
        for isin in isins:
            updates.append((fam_id, isin))
        display_name = norm_name.title() if norm_name else (company.title() or fam_id)
        family_data.append((fam_id, display_name, len(isins)))

    if not updates:
        print("  [FamilyBuilder] Sin actualizaciones necesarias")
        return 0

    # Estadisticas
    multi_class = sum(1 for g in groups.values() if len(g) > 1)
    total_isins  = sum(len(g) for g in groups.values())
    print(f"  [FamilyBuilder] {len(groups)} familias identificadas "
          f"({multi_class} con multiples clases) | {total_isins} ISINs")

    if dry_run:
        # Mostrar ejemplos de familias multi-clase
        print("  [FamilyBuilder] DRY-RUN -- ejemplos de familias multi-clase:")
        shown = 0
        for (company, norm), isins in sorted(groups.items()):
            if len(isins) > 1 and shown < 5:
                print(f"    [{company}] '{norm}' -> {isins}")
                shown += 1
        return len(updates)

    # Actualizar en batch
    ph = "%s"
    _ensure_family_rows(conn, family_data)
    executemany(
        conn,
        f"UPDATE fund_master SET fund_family_id = {ph} WHERE ISIN = {ph}",
        updates,
    )
    conn.commit()
    print(f"  [FamilyBuilder] {len(updates)} fondos actualizados con fund_family_id")

    # ── Corrección escalable de inconsistencias ──────────────────────────────
    # Aplica reglas basadas en atributos (calidad, mayoría) — sin nombres específicos
    correct_family_inconsistencies(conn, dry_run=dry_run)

    # ── Poblar tabla fund_families ────────────────────────────────────────────
    # Debe ejecutarse después de las correcciones para reflejar Fund_Nature corregida
    _populate_fund_families(conn, family_data)

    # ── Validación post-corrección ────────────────────────────────────────────
    inconsistencias = _validate_family_consistency(conn)
    # Separar las conocidas (heterogeneidad estructural documentada) del resto
    known     = [(f, n, nm) for f, n, nm in inconsistencias if _is_known_heterogeneous(nm)]
    unknown   = [(f, n, nm) for f, n, nm in inconsistencias if not _is_known_heterogeneous(nm)]
    n_total   = len(inconsistencias)
    n_incons  = len(unknown)
    if unknown:
        print(f"  [FamilyBuilder] AVISO: {n_incons} familias "
              f"con Fund_Nature inconsistente (ver log):")
        for fam_id, natures, nombres in unknown[:10]:
            print(f"    {fam_id} — natures={natures}")
            for n in nombres[:3]:
                print(f"      {n}")
        if n_incons > 10:
            print(f"    ... y {n_incons-10} mas")
    else:
        print("  [FamilyBuilder] Validacion OK — todas las familias son homogeneas")
    if known:
        # No-op para nuevas; solo confirmar recuento de conocidas
        print(f"  [FamilyBuilder] {len(known)} familias con heterogeneidad estructural "
              f"conocida (suprimidas del AVISO): "
              + ", ".join(f for f, _, _ in known))

    return len(updates)


def _ensure_family_rows(conn, family_data: list[tuple]) -> None:
    """Postgres only: family ids are re-issued sequentially on every run, and the FK from
    fund_master.fund_family_id needs the parent row to exist BEFORE fund_master is repointed."""
    from datetime import datetime as _dt, timezone as _tz
    now_str = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")
    executemany(
        conn,
        "INSERT INTO fund_families (family_id, family_name, fund_nature, n_funds, updated_at) "
        "VALUES (%s, %s, NULL, %s, %s) ON CONFLICT (family_id) DO NOTHING",
        [(fam_id, name, n, now_str) for fam_id, name, n in family_data],
    )


def _populate_fund_families(
    conn: "psycopg.Connection",
    family_data: list[tuple],
) -> int:
    """
    Inserta/reemplaza filas en fund_families usando los grupos calculados
    en build_fund_families().  Se llama después de correct_family_inconsistencies
    para que Fund_Nature refleje los valores ya corregidos en fund_master.
    """
    from datetime import datetime as _dt, timezone as _tz
    from collections import Counter as _Counter

    now_str = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")

    # Naturaleza dominante por familia (tras correcciones)
    nat_rows = conn.execute(
        "SELECT fund_family_id, Fund_Nature, COUNT(*) "
        "FROM fund_master WHERE fund_family_id IS NOT NULL "
        "GROUP BY fund_family_id, Fund_Nature"
    ).fetchall()
    nat_counts: dict = {}
    for fam_id, nature, cnt in nat_rows:
        if nature:
            nat_counts.setdefault(fam_id, _Counter())[nature] += cnt
    family_natures = {
        fid: c.most_common(1)[0][0]
        for fid, c in nat_counts.items() if c
    }

    rows = [
        (fam_id, name, family_natures.get(fam_id), n, now_str)
        for fam_id, name, n in family_data
    ]
    # fund_master.fund_family_id has a real FK to fund_families on Postgres (SQLite never
    # enforced it), so DELETE-everything-then-INSERT is rejected: upsert, then drop only the
    # families that no fund references any more.
    executemany(
        conn,
        "INSERT INTO fund_families (family_id, family_name, fund_nature, n_funds, updated_at) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (family_id) DO UPDATE SET "
        "family_name = EXCLUDED.family_name, fund_nature = EXCLUDED.fund_nature, "
        "n_funds = EXCLUDED.n_funds, updated_at = EXCLUDED.updated_at",
        rows,
    )
    conn.execute("DELETE FROM fund_families WHERE family_id <> ALL(%s)", ([r[0] for r in rows],))
    conn.commit()
    print(f"  [FamilyBuilder] fund_families populated: {len(rows)} familias")
    return len(rows)


def _is_known_heterogeneous(names: list) -> bool:
    """True when every member of the family has a normalised stem listed in _KNOWN_HETEROGENEOUS_STEMS (stable across runs, unlike the ids)."""
    stems = {_normalize_name(n) for n in names if n}
    return bool(stems) and stems <= _KNOWN_HETEROGENEOUS_STEMS


def _validate_family_consistency(conn: "psycopg.Connection") -> list:
    """
    Detecta familias con mas de una Fund_Nature distinta.
    Devuelve lista de (fam_id, natures_set, nombres_lista).
    """
    rows = conn.execute("""
        SELECT fund_family_id, Fund_Nature, Fund_Name
        FROM fund_master
        WHERE fund_family_id IS NOT NULL
        ORDER BY fund_family_id
    """).fetchall()

    from collections import defaultdict
    families: dict = defaultdict(lambda: {"natures": set(), "names": []})
    for fam_id, nature, name in rows:
        if nature:
            families[fam_id]["natures"].add(nature)
        families[fam_id]["names"].append(name or "")

    inconsistentes = []
    for fam_id, data in sorted(families.items()):
        if len(data["natures"]) > 1:
            inconsistentes.append((
                fam_id,
                sorted(data["natures"]),
                data["names"],
            ))

    return inconsistentes


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    import argparse
    from shared.backlog_client import install_excepthook
    install_excepthook(object_name="fund_family_builder.py")   # unhandled failure -> backlog ticket

    parser = argparse.ArgumentParser(
        description="Asigna fund_family_id agrupando clases del mismo fondo"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra grupos pero no escribe en BD"
    )
    args = parser.parse_args()

    # Postgres migration Stage 9 (found 2026-09-23 by auditing every raw sqlite3.connect): this block
    # opened SQLite directly, and P1_discoverAllFunds.bat runs it as `python -m
    # proyecto1.core.fund_family_builder` at the end of every P1 cycle. After cutover the rest of P1
    # would write to Postgres while THIS step silently kept rebuilding families in the retired SQLite
    # — exit code 0, no error, split-brain. get_connection() resolves the backend (flag, else
    # FONDOS_DB_BACKEND) and raises FileNotFoundError itself for a missing SQLite file.
    from shared.db import get_connection
    try:
        _conn = get_connection()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    print("BD: Postgres (FONDOS_PG_DSN)")
    n = build_fund_families(_conn, dry_run=args.dry_run)
    print(f"Total: {n} fondos procesados")
    _conn.close()
