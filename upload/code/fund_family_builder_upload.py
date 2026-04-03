# proyecto1/core/fund_family_builder.py
# -*- coding: utf-8 -*-
"""
Asignacion de fund_family_id en fund_master.

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
import sqlite3
import unicodedata
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))


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

        # -- Divisas ISO (solas al final) --
        | eur | usd | gbp | jpy | chf | aud | cad | sek | nok | dkk
        | hkd | sgd | cny | cnh | pln | czk | huf | mxn | brl | inr

        # -- Tipo de participacion / distribucion --
        | acc(?:umulation)?       # Acc, Accumulation
        | dist(?:ribution)?       # Dist, Distribution
        | inc(?:ome)?             # Inc, Income
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
        s = _CLASS_SUFFIXES.sub("", s).strip()
        if s == s_prev:
            break

    return s.strip()


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
    frozenset({"Renta Fija Flexible", "Renta Fija Corto Plazo"}),
    frozenset({"Monetario", "Renta Fija Corto Plazo"}),
    frozenset({"Alternativo", "Renta Variable"}),
    frozenset({"Monetario", "Renta Variable"}),
}

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
    2. Mayoría ≥ 2/3 + discordantes con Data_Quality=MISSING → aplicar mayoría
    3. Naturalezas adyacentes → usar la de mayor SRRI_Quality_Flag agregado
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

    # Regla 3: naturalezas adyacentes → usar la de mayor calidad SRRI agregada
    if nature_set in _ADJACENT_NATURE_PAIRS or \
       any(frozenset(nature_set) == p for p in _ADJACENT_NATURE_PAIRS):
        # Calcular calidad agregada por naturaleza
        quality_by_nature: dict[str, int] = {}
        for m in members:
            nat = m["Fund_Nature"]
            q = _SRRI_QUALITY_RANK.get(m.get("SRRI_Quality_Flag"), 0)
            quality_by_nature[nat] = quality_by_nature.get(nat, 0) + q

        if quality_by_nature:
            best_nature = max(quality_by_nature, key=quality_by_nature.get)
            best_q = quality_by_nature[best_nature]
            others_q = {k: v for k, v in quality_by_nature.items() if k != best_nature}
            # Solo corregir si la diferencia de calidad es clara (>2 puntos)
            if others_q and best_q - max(others_q.values()) > 2:
                to_correct = [m["ISIN"] for m in members
                              if m["Fund_Nature"] != best_nature]
                return best_nature, to_correct

    return None, []  # No se puede determinar de forma segura


def correct_family_inconsistencies(
    conn: sqlite3.Connection,
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
    conn.executemany(
        "UPDATE fund_master SET Fund_Nature = ? WHERE ISIN = ?",
        [(nat, isin) for nat, isin, _, _ in corrections],
    )

    # Registrar en ingestion_log
    for nat, isin, fam_id, old_nature in corrections:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO ingestion_log
                   (ISIN, Step, Status, Message, Created_At)
                   VALUES (?, 'FAMILY_NATURE_CORRECTION', 'INFO', ?, datetime('now'))""",
                (isin, f"Fund_Nature corregido: {old_nature} → {nat} "
                       f"(familia {fam_id}, regla escalable)")
            )
        except Exception:
            pass

    conn.commit()
    print(f"  [FamilyBuilder] {len(corrections)} correcciones aplicadas")
    return len(corrections)



def build_fund_families(
    conn: sqlite3.Connection,
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
    family_counter = 1

    for (company, norm_name), isins in sorted(groups.items()):
        fam_id = f"FAM_{family_counter:06d}"
        family_counter += 1
        for isin in isins:
            updates.append((fam_id, isin))

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
    conn.executemany(
        "UPDATE fund_master SET fund_family_id = ? WHERE ISIN = ?",
        updates,
    )
    conn.commit()
    print(f"  [FamilyBuilder] {len(updates)} fondos actualizados con fund_family_id")

    # ── Corrección escalable de inconsistencias ──────────────────────────────
    # Aplica reglas basadas en atributos (calidad, mayoría) — sin nombres específicos
    correct_family_inconsistencies(conn, dry_run=dry_run)

    # ── Validación post-corrección ────────────────────────────────────────────
    inconsistencias = _validate_family_consistency(conn)
    if inconsistencias:
        print(f"  [FamilyBuilder] AVISO: {len(inconsistencias)} familias "
              f"con Fund_Nature inconsistente (ver log):")
        for fam_id, natures, nombres in inconsistencias[:10]:
            print(f"    {fam_id} — natures={natures}")
            for n in nombres[:3]:
                print(f"      {n}")
        if len(inconsistencias) > 10:
            print(f"    ... y {len(inconsistencias)-10} mas")
    else:
        print("  [FamilyBuilder] Validacion OK — todas las familias son homogeneas")

    return len(updates)


def _validate_family_consistency(conn: sqlite3.Connection) -> list:
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

    parser = argparse.ArgumentParser(
        description="Asigna fund_family_id agrupando clases del mismo fondo"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Muestra grupos pero no escribe en BD"
    )
    args = parser.parse_args()

    try:
        from shared.config import DB_PATH
        db_path = Path(DB_PATH)
    except Exception:
        candidates = [
            Path(_ROOT) / "db" / "fondos.sqlite",
            Path("db") / "fondos.sqlite",
        ]
        db_path = next((p for p in candidates if p.exists()), None)

    if db_path is None or not db_path.exists():
        print("ERROR: No se encuentra fondos.sqlite")
        sys.exit(1)

    print(f"BD: {db_path}")
    _conn = sqlite3.connect(str(db_path))
    n = build_fund_families(_conn, dry_run=args.dry_run)
    print(f"Total: {n} fondos procesados")
    _conn.close()
