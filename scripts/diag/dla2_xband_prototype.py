# -*- coding: utf-8 -*-
"""
scripts/diag/dla2_xband_prototype.py  v3.3  -- BL-DLA-2-XBAND (prototipo)
=============================================================
v3.2 (2026-06-04, BL-DLA-2 NFD-accent root fix, Fidelity ONLY_RULED): algunas
    gestoras (FIL/Fidelity y otras) emiten acentos DESCOMPUESTOS (NFD: "o"+U+0301)
    en vez del precompuesto U+00F3. Las clases [oó] de TODOS los patrones fallaban
    -> la seccion "Composicion" no abria -> bands-X devolvia {} (cluster ONLY_RULED
    de ~169 fondos LU, dominante FIL). Fix de causa raiz: normalizar NFC el texto
    de cada word en extract_cat2a_xband (cubre composicion/gestion/operacion de una
    vez). Validado: 5 plantillas FIL ONLY_RULED -> AGREE@truth, 12 anchors OK.
=============================================================
v3.1 (2026-06-03, BL-DLA-2 OC dual-extraction batch): (C) etiqueta de
    operacion ampliada a "Costes de transaccion" + variantes FR (GAMCO
    LU0687943661/944396). (D) los bloques no-OC (entrada/salida/exito) pre-
    consumen su linea-valor inmediata (si/si+-1) antes del bucle OC, evitando
    que un bloque OC robe el 0% de una linea de salida en un empate de
    distancia (FR001400RZ04: gestion=0.00 -> 1.95). Validado 11 anchors + 4
    targets sin regresion (Nordea LU0173776047 pendiente de re-subir).
=============================================================
v2.8 (2026-05-31, BL-DLA-2 nearest-value assignment): reemplaza la lógica de
    bound/lookback de v2.7 por ASIGNACIÓN POR CERCANÍA con consumo único. El %
    de un componente _OC_* puede ir ENCIMA (ES0125756009, DWS, IE, FR) o DEBAJO
    (LU1133289592) de su etiqueta y a distancia variable; v2.7 fallaba cuando un
    bloque tomaba el valor de otro (0.29+0.29, 0.15+0.15). Cada bloque OC reclama
    ahora la línea-valor MÁS CERCANA a su etiqueta (cualquier dirección), acotada
    por fronteras vecinas (bloque/cabecera de grupo) y NO consumida por otro
    bloque. Validado 9 PDFs (ES/DE/Vanguard/Amundi/Vontobel/PIMCO/Nordea/Schroder/
    Artemis) sin regresión. PENDIENTE: regresión sobre los 15 ground-truth.
v2.7 (2026-05-30, BL-DLA-2 value-above-label): corrige dos sobre-extracciones
    donde un bloque _OC_* absorbía hacia delante el valor de un componente o
    grupo POSTERIOR cuyo % va ENCIMA de su etiqueta (orphan-above):
      - IE0002639551 (Vanguard): gestión robaba el % de operación -> 0.04+0.04
        (real 0.12+0.04 = 0.16). El % de gestión (0.12, orphan encima) se perdía.
      - FR0010251660 (Amundi): operación robaba la performance-fee 10,00% ->
        0.12+10.00 (real 0.12+0.03 = 0.15). Dos causas: (a) absorción cruzaba
        el orphan-above del bloque siguiente; (b) la cabecera "Costes accesorios"
        iba CENTRADA (x0>138) -> left vacío -> no se clasificaba como grupo y no
        cerraba el bloque de operación.
    Fix: (1) acotar el cuerpo de cada bloque _OC_* a [start_idx .. frontera-2],
    siendo frontera el primer bloque o cabecera de grupo siguiente, excluyendo
    así el orphan-above del bloque siguiente; (2) detectar la cabecera de grupo
    contra full_text aunque left esté vacío (devuelve is_group=True, solo cierra
    bloques, nunca abre componente). El lookback de rescate orphan-above se
    mantiene para DWS/IE/FR. Validado 5 PDFs (Vanguard/Amundi/Vontobel/PIMCO/
    Nordea) sin regresión. PENDIENTE: regresión sobre los 15 ground-truth +
    Polar/Candriam antes de despliegue corpus. (IE0002458671 layout [0,84%]
    sigue sin extraer en bands-X; lo cubre el fallback ruled_text -> ONLY_RULED,
    no CONFLICT.)
=============================================================
v2.6 (2026-05-30, BL-DLA-2 Patrón A): corrige la pérdida del componente de
    gestión cuando la etiqueta "Comisiones de\ngestión y otros costes..." se
    parte en líneas y la línea-2 comparte fila visual con el valor (1.60 %).
    El reensamblaje de etiqueta (lookahead) rompía al detectar % en la línea
    siguiente, descartando la continuación y dejando OC = solo operación
    (0.01). Verificado LU0329630130: 0.01 -> 1.61 (1.60+0.01). Esperado:
    resuelve los 4 fondos Patrón A (Vontobel/Variopartner). PENDIENTE regresión
    sobre los 15 ground-truth + Polar/Candriam/DWS antes de despliegue corpus.
=============================================================
Prototipo del extractor de tablas Cat.2A por RECONSTRUCCIÓN DE BANDAS X.

MOTIVACIÓN (diagnóstico 2026-05-24):
    El serializador actual depende de pdfplumber.find_tables(), que FALLA en los
    KIID PRIIPs cuyas tablas no tienen líneas de rejilla y separan columnas solo
    por espacios (~433 fondos serializer_no_cat2a + ~792 cat2a_sin_valor). En
    esos PDFs:
      - Las columnas están en bandas X constantes (etiqueta ~30, descripción
        ~140, importe EUR ~490-550), sin carácter separador.
      - Una fila lógica ("Comisiones de gestión y otros costes administrativos
        de funcionamiento") se parte en VARIAS subfilas por altura porque la
        columna-etiqueta es estrecha. find_tables() trata cada subfila como fila
        independiente y asocia mal etiqueta/valor.
      - El OC total NO aparece como tal: es la SUMA de "Comisiones de gestión"
        (p.ej. 2,65%) + "Costes de operación" (p.ej. 2,05%) = 4,70%.

    Verificado: LU0213962813 tiene OC real 4,70% pero BD guarda 2,73% (solo
    capturó el componente de gestión). Es decir, este fix no solo recupera
    LOW->HIGH: corrige valores YA registrados como aceptables pero erróneos.

ALGORITMO (hipótesis del usuario, validada sobre PDF real):
    1. extract_words por página; agrupar en líneas visuales por 'top'.
    2. Acotar a la sección "Composición de los costes" (inicio cabecera Cat.2A,
       fin = siguiente sección "¿Cuánto tiempo...?" / "¿Cómo reclamar...?").
    3. Detectar columnas: clustering de x0 en bandas; reducir a 3 conceptuales
       (etiqueta / descripción / importe). El separador es el salto de X > umbral
       (equivalente a >=3 espacios pedidos por el usuario).
    4. Fusión de subfilas: una línea cuyo contenido cae SOLO en la columna
       etiqueta (sin descripción ni importe) es CONTINUACIÓN de la fila lógica
       abierta; se concatena a su etiqueta.
    5. Clasificar cada fila lógica a etiqueta canónica y extraer % (preferente)
       o importe EUR. Consolidar OC = gestión + operación.

ALCANCE / ESTADO:
    PROTOTIPO autónomo y NO intrusivo: NO modifica dla_table_serializer.py.
    Objetivo: medir la tasa de recuperación real sobre PDFs antes de decidir
    integrarlo como "Estrategia 1.5" en el serializador (Go/No-Go por datos).
    Incluye PDFs de control OK_HEURISTIC para verificar que no introduce ruido.

USO:
    # Sobre una carpeta de PDFs (nombre = ISIN.pdf):
    python dla2_xband_prototype.py --pdf-dir ./muestra_pdfs

    # Cruzando con BD para comparar contra el valor actual (opcional):
    python dla2_xband_prototype.py --pdf-dir ./muestra_pdfs --db ruta.sqlite

    # Un solo PDF con traza detallada:
    python dla2_xband_prototype.py --pdf C:\\ruta\\LU0213962813.pdf --debug

SALIDA:
    - Consola + log: dla2_xband_prototype.log
    - CSV: dla2_xband_prototype.csv (ISIN, componentes extraídos, OC, comparación BD)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from collections import defaultdict
from io import StringIO
from pathlib import Path
from typing import Optional

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False


# ──────────────────────────────────────────────────────────────────────────────
# Patrones (tolerantes a TEXTO PEGADO: usan \s* en vez de \s+, porque
# extract_words concatena y el KIID a menudo viene sin espacios entre palabras)
# ──────────────────────────────────────────────────────────────────────────────

_PAT_SECTION_START = re.compile(
    r'composici[oó]n\s*de\s*(?:los\s*)?(?:costes?|gastos)'
    r'|composition\s*of\s*(?:the\s*)?costs?',
    re.IGNORECASE,
)
# v3.3 (BL-DLA-2 section-open): el encabezado "Composición de los costes" NO
# abría en dos clases reales (cluster ONLY_RULED con bands-X silenciado):
#   (a) T. Rowe Price y otras: la tabla de costes NO lleva ese encabezado;
#       empieza directamente en "Costes únicos..." / "Costes corrientes...".
#   (b) UBS White Fleet: el encabezado se renderiza con glifos DUPLICADOS
#       ("CCoommppoossiicciióónn ddee llooss ccoosstteess"), de modo que el
#       patrón no casa. El CUERPO de la tabla sí está limpio.
# Solución: (1) ancla de respaldo en el sub-encabezado "Costes corrientes
# detraídos cada año" (no duplicado, presente en ambas clases y situado justo
# encima de gestión -> abrir ahí captura exactamente gestión+operación = OC);
# (2) probar una variante "des-duplicada" de cada línea antes de descartar.
_PAT_SECTION_START_FALLBACK = re.compile(
    r'costes?\s*corrientes\s*detra[ií]dos'
    r'|ongoing\s*costs?\s*taken',
    re.IGNORECASE,
)
_RE_DOUBLED_GLYPH = re.compile(r'(.)\1')


def _dedupe_glyphs(txt: str) -> str:
    """Colapsa glifos duplicados (artefacto de render en negrita: 'CCoo..' ->
    'Co..'). Solo se usa como ÚLTIMO intento de apertura de sección, nunca
    sobre el cuerpo, para no corromper dobles legítimas ('ll', 'rr', 'cc')."""
    return _RE_DOUBLED_GLYPH.sub(r'\1', txt)


_PAT_SECTION_END = re.compile(
    r'cu[aá]nto\s*tiempo|how\s*long'
    r'|c[oó]mo\s*puedo\s*reclamar|how\s*can\s*i\s*complain'
    r'|otros\s*datos\s*de\s*inter[eé]s|other\s*relevant\s*information',
    re.IGNORECASE,
)

# Etiquetas-componente → canónica. Casan al INICIO (^) de la columna izquierda.
# Orden importa: cabeceras de grupo (canon=None) y gestión/operación antes que
# el OC genérico. Las cabeceras de grupo delimitan bloques pero no son componentes.
# Los componentes _OC_* se SUMAN para formar "Costes corrientes".
_COMPONENT_LABELS = [
    # Cabeceras de grupo (canon=None): cierran el bloque previo, no abren componente.
    (re.compile(r'^costes?\s*[uú]nicos', re.I), None),
    (re.compile(r'^costes?\s*corrientes?\s*detra', re.I), None),
    (re.compile(r'^costes?\s*accesorios', re.I), None),
    # Componentes reales.
    (re.compile(r'^costes?\s*de\s*entrada|^entry\s*(?:cost|charge|fee)', re.I),
     "Costes de entrada"),
    (re.compile(r'^costes?\s*de\s*salida|^exit\s*(?:cost|charge|fee)', re.I),
     "Costes de salida"),
    (re.compile(r'^comisiones?\s*de\s*gesti[oó]n|^gastos?\s*de\s*gesti[oó]n'
                r'|^management\s*fees?', re.I),
     "_OC_GESTION"),
    # v3.1 (BL-DLA-2, GAMCO LU0687943661/944396): la línea de operación puede
    # rotularse "Costes de transacción" y/o describirse en FRANCÉS. Ampliar la
    # etiqueta canónica de operación a transacción + variantes FR/EN.
    (re.compile(r'^costes?\s*de\s*(?:operaci[oó]n|transacci[oó]n)'
                r'|^gastos?\s*de\s*(?:operaci[oó]n|transacci[oó]n)'
                r'|^co[uû]ts?\s*de\s*(?:transaction|n[ée]gociation)'
                r'|^frais\s*de\s*transaction'
                r'|^transaction\s*costs?', re.I),
     "_OC_OPERACION"),
    (re.compile(r'^costes?\s*corrientes?$|^ongoing\s*charges?|^gastos\s*corrientes?', re.I),
     "Costes corrientes"),
    (re.compile(r'^comisi[oó]n(?:es)?\s*de\s*(?:[eé]xito|rendimiento)'
                r'|^performance\s*fees?', re.I),
     "Comisión de éxito"),
]

# % con decimales (5,00% / 0.74%) o cero explícito (0% / 0,00%).
_PAT_PCT = re.compile(r'(\d{1,3}[,\.]\d{1,4})\s*%|\b(0)\s*[%％]')
# Importe en moneda.
_PAT_EUR = re.compile(r'(\d[\d.,]*)\s*(EUR|USD|GBP|CHF|€|\$)', re.IGNORECASE)
# Texto que declara coste cero ("no cobramos", "ninguna", "nil").
_PAT_ZERO_WORD = re.compile(
    r'no\s*cobramos|ningun[ao]|no\s*(?:hay|existe|aplica)|\bnil\b|\bnone\b',
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────────────────────
# Logger dual
# ──────────────────────────────────────────────────────────────────────────────

class _DualLogger:
    def __init__(self):
        self._buf = StringIO()

    def w(self, text: str = ""):
        print(text)
        self._buf.write(text + "\n")

    def flush_to_file(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._buf.getvalue(), encoding="utf-8")
        print(f"\n[LOG] Guardado en: {path}")


log = _DualLogger()


# ──────────────────────────────────────────────────────────────────────────────
# Núcleo: extracción por bandas X
# ──────────────────────────────────────────────────────────────────────────────

def _classify_component_label(left_text: str, full_text: str = ""):
    """
    Clasifica el texto de la columna IZQUIERDA en etiqueta canónica.
    Devuelve (canon, is_header_group):
      - canon: etiqueta canónica de componente, o None.
      - is_header_group: True si es una cabecera de grupo (Costes únicos /
        Costes corrientes detraídos... / Costes accesorios) que NO es un
        componente pero SÍ delimita bloques.

    BUG FIX (BL-DLA-2-XBAND v2.1): la columna izquierda a menudo se trunca a
    "Costes corrientes" porque "detraídos cada año" cae en x>138. Sin mirar
    la línea completa, el clasificador confunde la cabecera de grupo con el
    componente directo "Costes corrientes", abre un bloque espurio que absorbe
    el valor de "Comisiones de gestión" siguiente, y descarta el componente
    de operación porque "Costes corrientes" ya está en `result`.
    Solución: comprobar cabeceras de grupo contra el TEXTO COMPLETO de la línea
    (no solo la columna izquierda) antes de evaluar componentes.
    """
    t = left_text.strip()
    # v2.7 (BL-DLA-2, Amundi FR0010251660): la cabecera de grupo "Costes
    # accesorios detraídos..." puede ir CENTRADA (x0>xlabel_max) -> left vacío.
    # El guard "if not t" la descartaba y el bloque de operación absorbía la
    # performance-fee (10,00%). Comprobar la cabecera de grupo contra full_text
    # ANTES del guard. Devuelve is_group=True (solo CIERRA bloques, nunca abre
    # componente), por lo que es seguro.
    full = (full_text or t).lower()
    _GRP = (r'costes?\s*corrientes?\s*detra|costes?\s*corrientes?\s*\[?detra'
            r'|costes?\s*corrientes?\s*anuales|costes?\s*recurrentes?\s*detra'
            r'|costes?\s*[uú]nicos\s*de\s*entrada|costes?\s*accesorios')
    if re.search(_GRP, full):
        return None, True  # cabecera de grupo
    if not t:
        return None, False
    for pat, canon in _COMPONENT_LABELS:
        if pat.search(t):
            return canon, (canon is None)
    return None, False


def extract_cat2a_xband(page, debug: bool = False, xlabel_max: float = 138.0) -> dict:
    """
    Extrae componentes de coste Cat.2A por AGRUPACIÓN EN BLOQUES entre etiquetas.

    A diferencia de la v1 (que clasificaba columna por línea y fallaba cuando el
    valor estaba en una línea distinta de la etiqueta), la v2 agrupa por BLOQUE
    LÓGICO: una fila empieza cuando la columna izquierda (x0 < xlabel_max) casa
    una etiqueta-componente, y absorbe TODAS las líneas siguientes (con su % y
    sus importes, vengan en la banda que vengan) hasta la siguiente etiqueta.
    Esto reconstruye filas multi-línea como "Comisiones de gestión y otros
    costes administrativos de funcionamiento ... 1,04% ... 105 USD".

    Devuelve dict {canonical_label: value_str}; consolida OC = gestión+operación.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return {}

    # v3.2 (BL-DLA-2 NFD accents, Fidelity FIL): algunas gestoras emiten los
    # acentos DESCOMPUESTOS (NFD): "ó" = 'o' + U+0301 (combining acute), no el
    # codepoint precompuesto U+00F3. Las clases [oó] de TODOS los patrones
    # (sección, etiquetas) fallan contra la secuencia descompuesta, de modo que
    # la sección "Composición" no abría y bands-X devolvía {} (causa del cluster
    # ONLY_RULED de ~169 fondos FIL). Normalizar a NFC en origen arregla la clase
    # entera de una vez (composición/gestión/operación/...), no solo el título.
    for w in words:
        w["text"] = unicodedata.normalize("NFC", w["text"])

    lines = defaultdict(list)
    for w in words:
        lines[round(w["top"])].append(w)
    ordered = sorted(lines)

    # 1. Acotar a la sección "Composición de los costes". v3.3: tres intentos
    #    de apertura por línea -> patrón primario; patrón primario sobre la
    #    línea des-duplicada (encabezado UBS con glifos dobles); ancla de
    #    respaldo "Costes corrientes detraídos" (clases sin encabezado).
    start = end = None
    for top in ordered:
        txt = "".join(w["text"] for w in sorted(lines[top], key=lambda x: x["x0"]))
        if start is None and (
            _PAT_SECTION_START.search(txt)
            or _PAT_SECTION_START.search(_dedupe_glyphs(txt))
            or _PAT_SECTION_START_FALLBACK.search(txt)
        ):
            start = top
        elif start is not None and _PAT_SECTION_END.search(txt):
            end = top
            break
    if start is None:
        return {}
    if end is None:
        end = ordered[-1] + 1
    section = [t for t in ordered if start <= t < end]

    # 2. Recorrer la sección agrupando en bloques entre etiquetas-componente.
    #    Cada línea visual se guarda con su texto completo para poder, en la
    #    fase 3, rescatar valores "huérfanos" que aparezcan en la línea ANTERIOR
    #    a la etiqueta (layout DWS: el % de operación va encima de su rótulo).
    section_text = {}   # top -> texto completo de la línea
    blocks = []         # {canon, text, start_idx} ; start_idx = posición en 'section'
    group_hdr_idx = []  # índices de cabeceras de grupo (cierran bloque OC)
    cur = None
    for idx, t in enumerate(section):
        ws = sorted(lines[t], key=lambda x: x["x0"])
        left = " ".join(w["text"] for w in ws if w["x0"] < xlabel_max)
        full = " ".join(w["text"] for w in ws)
        section_text[idx] = full

        # v2.2: las etiquetas de componente pueden estar partidas en 2-3 líneas
        # consecutivas (p.ej. "Comisiones de\n  gestión y otros costes\n
        # administrativos..."). Para clasificar la línea ACTUAL, combinamos su
        # left con el de las siguientes 2 líneas si esas son continuación de
        # etiqueta (left poblado, sin valor en columna derecha y no son otra
        # cabecera componente).
        combined_left = left
        for lookahead in range(1, 3):
            j = idx + lookahead
            if j >= len(section):
                break
            ws_j = sorted(lines[section[j]], key=lambda x: x["x0"])
            left_j = " ".join(w["text"] for w in ws_j if w["x0"] < xlabel_max)
            full_j = " ".join(w["text"] for w in ws_j)
            # parar si la siguiente línea ya es otra etiqueta componente o un
            # valor obvio (% / EUR en su parte central)
            if not left_j.strip():
                break
            # v2.6 (BL-DLA-2 Patrón A fix, LU0329630130): una línea de
            # CONTINUACIÓN de etiqueta puede compartir su fila visual con el
            # valor (layout PRIIPs estándar: "Comisiones de\ngestión y otros
            # costes 1.60 %..."). El break anterior por %/EUR descartaba esa
            # línea e impedía reensamblar la etiqueta de gestión -> OC perdía
            # el componente de gestión. Criterio correcto (alineado con la
            # intención original): es continuación si la columna izquierda
            # contiene TEXTO de etiqueta (>=3 letras seguidas); solo se rompe
            # ante una línea de valor PURA (sin texto alfabético) o ante una
            # nueva etiqueta componente/cabecera.
            if not re.search(r'[A-Za-zÁÉÍÓÚáéíóúÑñ]{3,}', left_j):
                break
            # parar si la línea siguiente abre otro componente (e.g. operación)
            canon_j, grp_j = _classify_component_label(left_j, full_j)
            if canon_j or grp_j:
                break
            combined_left = (combined_left + " " + left_j).strip()

        canon, is_group = _classify_component_label(combined_left, full)
        # Una línea es "etiqueta conocida" si: (a) clasifica como componente, o
        # (b) es cabecera de grupo (canon=None pero is_group=True). En ambos
        # casos cierra el bloque actual; solo (a) abre uno nuevo.
        is_known_left = (canon is not None) or is_group
        if is_known_left:
            if canon:
                cur = {"canon": canon, "text": full, "start_idx": idx}
                blocks.append(cur)
            else:
                cur = None  # cabecera de grupo: cierra bloque, no abre componente
                group_hdr_idx.append(idx)
        else:
            if cur is not None:
                cur["text"] += " " + full

    # 3. Extraer valores de cada bloque y consolidar OC.
    # v2.7 (BL-DLA-2 value-above-label, Vanguard IE0002639551 / Amundi
    #   FR0010251660): la absorción hacia delante (fase 2) hacía que un bloque
    #   _OC_* tragara el valor ORPHAN-ABOVE del componente SIGUIENTE (cuyo % va
    #   en la línea encima de su etiqueta) o un valor de un grupo posterior
    #   (comisión de rendimiento). Resultado: gestión robaba el % de operación
    #   (IE: 0.04+0.04) u operación robaba la performance-fee (FR: 0.12+10.00).
    #   Fix: para _OC_*, ACOTAR el texto del bloque a [start_idx .. next-2], de
    #   modo que nunca incluya la línea next-1 (posición orphan-above del bloque
    #   siguiente). El lookback de rescate (orphan-above del PROPIO bloque) se
    #   mantiene intacto para los layouts DWS/IE/FR donde el valor va encima.
    start_indices = sorted(b["start_idx"] for b in blocks)
    block_idx_set = set(start_indices)
    def _next_boundary(si):
        # primera frontera tras si: siguiente bloque (cuyo orphan-above en
        # boundary-1 hay que excluir) o siguiente cabecera de grupo (que cierra
        # el bloque OC; su línea NO contiene valor de componente).
        cand = [s for s in start_indices if s > si] + \
               [g for g in group_hdr_idx if g > si]
        return min(cand) if cand else len(section)

    result = {}
    oc_components = []
    # Asignación por CERCANÍA bidireccional con consumo único.
    #   El % de un componente _OC_* puede ir ENCIMA (ES0125756009, DWS, IE, FR)
    #   o DEBAJO (LU1133289592) de su etiqueta, a distancia variable. Regla
    #   robusta: para cada bloque OC, tomar la línea-valor más CERCANA a su
    #   etiqueta (en cualquier dirección), acotada por las fronteras vecinas
    #   (bloque o cabecera de grupo, arriba y abajo) y NO consumida por otro
    #   bloque. El consumo único evita que gestión y operación tomen el mismo %
    #   (causa del 0.29+0.29 / 0.15+0.15).
    oc_blocks = [b for b in blocks if b["canon"] in ("_OC_GESTION", "_OC_OPERACION")]
    def _prev_boundary(si):
        cand = [s for s in start_indices if s < si] + \
               [g for g in group_hdr_idx if g < si]
        return max(cand) if cand else -1
    # Recolectar líneas-valor candidatas con su % (excluye líneas de etiqueta
    # de bloque, que no portan el valor del componente salvo que lo lleven en su
    # propia línea —p.ej. Vontobel mgmt—; esas se tratan por cercanía 0).
    consumed = set()
    # v3.1 (BL-DLA-2 entry/exit value-bleed, FR001400RZ04): un bloque OC puede
    #   robar la línea-valor de un componente VECINO no-OC (entrada/salida/perf)
    #   cuando esa línea queda "libre" y empata en distancia con el valor real.
    #   Caso real: gestión (si=8) tenía como candidatos idx6 ("0 %", el 0,00% de
    #   la línea de SALIDA) e idx10 ("1,95 %", su valor real), ambos a distancia 2;
    #   el orden de iteración hacía ganar idx6 -> gestión=0.00 (CONFLICT). Causa:
    #   el bloque de salida no consumía su propia línea-valor (los no-OC se extraen
    #   después, desde b["text"], sin marcar consumed). Fix: PRE-consumir la
    #   línea-valor más cercana de cada bloque no-OC (entrada/salida/éxito) ANTES
    #   del bucle OC, con el mismo criterio de cercanía acotada. Así idx6 deja de
    #   estar libre y gestión toma idx10. No altera el OC de layouts ya correctos:
    #   solo retira de la reserva valores que pertenecen a componentes no-OC.
    #   Implementación: cada bloque no-OC reclama su línea-valor INMEDIATA (si o
    #   si±1) que porte %, en este orden de preferencia: misma línea (inline) ->
    #   línea siguiente (valor-debajo, p.ej. EdR salida) -> línea anterior. Solo el
    #   vecino inmediato: NO usar el rango ancho de cercanía (eso permitía que salida
    #   "alcanzara hacia atrás" el valor de entrada y lo robara, dejando libre el 0%
    #   propio para que gestión lo tomara). Acotado además por fronteras vecinas.
    nonoc_blocks = [b for b in blocks
                    if b["canon"] not in ("_OC_GESTION", "_OC_OPERACION")]
    for b in nonoc_blocks:
        si = b["start_idx"]
        lo = _prev_boundary(si)
        hi = _next_boundary(si)
        for cand in (si, si + 1, si - 1):
            if cand in consumed or cand <= lo or cand >= hi:
                continue
            v, p = _extract_value_from_block(section_text.get(cand, ""))
            if p is not None:
                consumed.add(cand)
                break
    # Procesar bloques OC en orden; cada uno reclama el valor libre más cercano.
    for b in oc_blocks:
        si = b["start_idx"]
        lo = _prev_boundary(si)          # exclusivo por abajo
        hi = _next_boundary(si)          # exclusivo por arriba (índice frontera)
        # rango admisible (lo, hi), excluyendo la propia frontera-bloque (su
        # orphan-above pertenece a ESE bloque) — pero permitiendo hi-1 si la
        # frontera es cabecera de grupo.
        hi_excl = hi - 1 if hi in block_idx_set else hi
        best = None  # (dist, idx, val_str, pct)
        # incluir la propia línea de etiqueta (dist 0) por si lleva el valor
        for idx in range(lo + 1, hi_excl):
            if idx in consumed:
                continue
            v, p = _extract_value_from_block(section_text.get(idx, ""))
            if p is None:
                continue
            dist = abs(idx - si)
            if best is None or dist < best[0]:
                best = (dist, idx, v, p)
        if best is not None:
            consumed.add(best[1])
            oc_components.append(best[3])
            # BL-DLA-2 (INTEGRATED_SPEC_v20_v2 §4.4): exponer el valor por
            # componente (gestión / operación) ETIQUETADO, para la arbitración
            # per-componente del flujo de producción. Estrictamente ADITIVO:
            # no altera oc_components (OC consolidado) ni "_OC_breakdown" ni
            # ningún camino existente. Solo añade dos claves opcionales al dict
            # de resultado que el comparador dual leerá si están presentes.
            if b["canon"] == "_OC_GESTION":
                result["_OC_GESTION_VAL"] = best[3]
            elif b["canon"] == "_OC_OPERACION":
                result["_OC_OPERACION_VAL"] = best[3]

    for b in blocks:
        canon = b["canon"]
        if canon in ("_OC_GESTION", "_OC_OPERACION"):
            continue
        val_str, pct = _extract_value_from_block(b["text"])
        if canon not in result and val_str:
            result[canon] = val_str

    if oc_components and "Costes corrientes" not in result:
        result["Costes corrientes"] = f"{sum(oc_components):.2f}".replace(".", ",") + " %"
        result["_OC_breakdown"] = "+".join(f"{c:.2f}" for c in oc_components)

    if debug:
        log.w(f"    sección: {len(section)} líneas, {len(blocks)} bloques, "
              f"OC components: {oc_components}")

    return result


def _extract_value_from_block(txt: str) -> tuple:
    """
    Extrae (valor_str, pct_float_o_None) del texto completo de un bloque.
    Preferencia: % explícito > cero declarado > importe en moneda.
    """
    m = _PAT_PCT.search(txt)
    if m:
        if m.group(1):
            num = m.group(1)
            return (f"{num} %", float(num.replace(",", ".")))
        return ("0 %", 0.0)
    if _PAT_ZERO_WORD.search(txt):
        return ("0 %", 0.0)
    m_eur = _PAT_EUR.search(txt)
    if m_eur:
        return (m_eur.group(0).strip(), None)  # EUR sin base no es convertible a %
    return ("", None)



def extract_from_open_pdf(pdf, debug: bool = False) -> dict:
    """
    Igual que extract_from_pdf pero sobre un pdfplumber.PDF YA ABIERTO (no
    reabre). Permite al harness dual compartir un único open por fondo
    (memoria/velocidad) sin perder el merge cross-page v2.9.
    """
    pages = pdf.pages[:3]
    best_single = None

    def _ncomp(res):
        bd = res.get("_OC_breakdown", "") if res else ""
        return len([c for c in bd.split("+") if c]) if bd else 0

    for pi, page in enumerate(pages):
        res = extract_cat2a_xband(page, debug=debug)
        if res and "Costes corrientes" in res:
            if _ncomp(res) >= 2:
                res["_page"] = pi + 1
                return res
            if best_single is None:
                best_single = (pi, res)
        elif res and best_single is None:
            best_single = (pi, res)
    best_n = _ncomp(best_single[1]) if best_single else 0
    for pi in range(len(pages) - 1):
        res = _extract_across_pages(pages[pi], pages[pi + 1], debug=debug)
        if res and "Costes corrientes" in res and _ncomp(res) > best_n:
            res["_page"] = pi + 1
            return res
    if best_single is not None:
        pi, res = best_single
        res["_page"] = pi + 1
        return res
    return {}


def extract_from_pdf(pdf_path: Path, debug: bool = False) -> dict:
    """
    Recorre las páginas (máx 3) y devuelve el primer resultado Cat.2A no vacío.
    Abre el PDF y delega en extract_from_open_pdf (que contiene el merge
    cross-page v2.9). Conservado para uso standalone/CLI.
    """
    with pdfplumber.open(str(pdf_path)) as pdf:
        return extract_from_open_pdf(pdf, debug=debug)


def _extract_from_pdf_legacy(pdf_path: Path, debug: bool = False) -> dict:
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages[:3]
        best_single = None

        def _ncomp(res):
            bd = res.get("_OC_breakdown", "") if res else ""
            return len([c for c in bd.split("+") if c]) if bd else 0

        # Intento 1: cada página por separado. Un resultado de UNA página solo se
        # acepta como definitivo si trae el OC COMPLETO (>=2 componentes, o 1
        # componente que sea el OC directo "Costes corrientes" sin desglose). Un
        # resultado de 1 SOLO componente _OC_* puede ser una tabla PARTIDA por
        # salto de página (gestión al final de pág N, operación en pág N+1, p.ej.
        # DWS LU2357626170: pág2=2.11 gestión, pág3=0.58 operación). En ese caso
        # NO se retorna aún; se intenta el merge cross-page (Intento 2) y se
        # prefiere si recupera 2 componentes.
        for pi, page in enumerate(pages):
            res = extract_cat2a_xband(page, debug=debug)
            if res and "Costes corrientes" in res:
                if _ncomp(res) >= 2:
                    res["_page"] = pi + 1
                    return res
                # 1 componente: posible tabla partida. Guardar y seguir.
                if best_single is None:
                    best_single = (pi, res)
            elif res and best_single is None:
                best_single = (pi, res)  # guardamos por si nada mejor aparece
        # Intento 2: secciones que cruzan página. Combinamos pares consecutivos
        # y damos prioridad a estos resultados sobre un parcial de una sola pág.
        # Se prefiere el merge solo si recupera MÁS componentes que el parcial.
        best_n = _ncomp(best_single[1]) if best_single else 0
        for pi in range(len(pages) - 1):
            res = _extract_across_pages(pages[pi], pages[pi + 1], debug=debug)
            if res and "Costes corrientes" in res and _ncomp(res) > best_n:
                res["_page"] = pi + 1
                return res
        # Fallback: si solo había un parcial de página única, devolverlo.
        if best_single is not None:
            pi, res = best_single
            res["_page"] = pi + 1
            return res
    return {}


def _extract_across_pages(page_a, page_b, debug: bool = False) -> dict:
    """
    Ejecuta extract_cat2a_xband sobre los words combinados de dos páginas
    consecutivas. Los words de page_b reciben un offset en `top` mayor que
    el último top de page_a para preservar el orden vertical.
    """
    class _FakePage:
        """Cumple el contrato mínimo: extract_words() devuelve la lista combinada."""
        def __init__(self, ws):
            self._ws = ws
        def extract_words(self, **kwargs):
            return self._ws

    wa = page_a.extract_words(use_text_flow=False, keep_blank_chars=False)
    wb = page_b.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not wa or not wb:
        return {}
    max_top_a = max(w["top"] for w in wa)
    offset = max_top_a + 50  # margen entre páginas
    wb_shifted = [{**w, "top": w["top"] + offset} for w in wb]
    combined = wa + wb_shifted
    return extract_cat2a_xband(_FakePage(combined), debug=debug)


# ──────────────────────────────────────────────────────────────────────────────
# Cruce opcional con BD (comparación contra valor actual)
# ──────────────────────────────────────────────────────────────────────────────

def _load_db(conn, isins):
    out = {}
    cols = "Ongoing_Charge_Recurrent, Entry_Fee_Pct, Exit_Fee_Pct, Cost_Extraction_Quality"
    isins = list(isins)
    for i in range(0, len(isins), 400):
        chunk = isins[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT ISIN,{cols} FROM fund_master WHERE ISIN IN ({ph})", chunk):
            out[r[0]] = {"oc": r[1], "entry": r[2], "exit": r[3], "quality": r[4]}
    return out


def _pct_to_ratio(val_str):
    """'4,70 %' -> 0.047 ; importes EUR -> None."""
    if not val_str:
        return None
    m = _PAT_PCT.search(val_str)
    if m:
        if m.group(1):
            return float(m.group(1).replace(",", ".")) / 100.0
        return 0.0
    return None


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main(pdf_dir=None, pdf=None, db_path=None, csv_path=None, log_path=None,
         project_root=None, debug=False):

    if not _HAS_PDFPLUMBER:
        print("[FATAL] pdfplumber no disponible.")
        sys.exit(2)

    # Resolver salidas.
    base = Path(project_root).resolve() if project_root else Path.cwd()
    csv_file = Path(csv_path) if csv_path else base / "dla2_xband_prototype.csv"
    log_file = Path(log_path) if log_path else base / "dla2_xband_prototype.log"

    # Recopilar PDFs.
    pdfs = []
    if pdf:
        pdfs = [Path(pdf)]
    elif pdf_dir:
        pdfs = sorted(Path(pdf_dir).glob("*.pdf"))
    else:
        print("[FATAL] indica --pdf-dir o --pdf.")
        sys.exit(2)

    log.w("BL-DLA-2-XBAND — Prototipo de extractor Cat.2A por bandas X")
    log.w(f"PDFs a procesar: {len(pdfs)}")

    # BD opcional.
    db = {}
    if db_path:
        if project_root and str(Path(project_root)) not in sys.path:
            sys.path.insert(0, str(Path(project_root)))
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            db = _load_db(conn, [p.stem for p in pdfs])
            log.w(f"BD cargada: {len(db)} ISINs encontrados en fund_master")
        except Exception as e:
            log.w(f"/!\\ No se pudo cargar BD ({e}); se omite comparación.")

    fields = ["ISIN", "page", "entry", "exit", "oc", "oc_breakdown", "perf",
              "oc_ratio_xband", "oc_db", "oc_match", "quality_db"]
    rows_out = []

    log.w("\n" + "=" * 72)
    for p in pdfs:
        isin = p.stem
        try:
            res = extract_from_pdf(p, debug=debug)
        except Exception as e:
            log.w(f"\n### {isin}: ERROR {type(e).__name__}: {e}")
            rows_out.append({"ISIN": isin, "page": "", "entry": "", "exit": "",
                             "oc": f"ERROR:{e}", "oc_breakdown": "", "perf": "",
                             "oc_ratio_xband": "", "oc_db": "", "oc_match": "",
                             "quality_db": ""})
            continue

        oc_xband = res.get("Costes corrientes", "")
        oc_ratio = _pct_to_ratio(oc_xband)
        dbinfo = db.get(isin, {})
        oc_db = dbinfo.get("oc")
        match = ""
        if oc_ratio is not None and oc_db is not None:
            try:
                match = "OK" if abs(oc_ratio - float(oc_db)) <= 0.0005 else \
                        f"DIFF({oc_ratio:.4f} vs {float(oc_db):.4f})"
            except (TypeError, ValueError):
                match = ""

        log.w(f"\n### {isin}  (pág {res.get('_page','-')})")
        if res:
            for k in ("Costes de entrada", "Costes de salida",
                      "Costes corrientes", "Comisión de éxito"):
                if k in res:
                    extra = ""
                    if k == "Costes corrientes" and res.get("_OC_breakdown"):
                        extra = f"  [= {res['_OC_breakdown']}]"
                    log.w(f"    {k}: {res[k]}{extra}")
            if oc_db is not None:
                log.w(f"    -- BD: OC={oc_db}  quality={dbinfo.get('quality')}  "
                      f"=> {match or 'sin comparar'}")
        else:
            log.w("    [SIN EXTRACCIÓN — no se detectó sección de composición]")

        rows_out.append({
            "ISIN": isin, "page": res.get("_page", ""),
            "entry": res.get("Costes de entrada", ""),
            "exit": res.get("Costes de salida", ""),
            "oc": oc_xband,
            "oc_breakdown": res.get("_OC_breakdown", ""),
            "perf": res.get("Comisión de éxito", ""),
            "oc_ratio_xband": f"{oc_ratio:.4f}" if oc_ratio is not None else "",
            "oc_db": oc_db if oc_db is not None else "",
            "oc_match": match,
            "quality_db": dbinfo.get("quality", ""),
        })

    # CSV.
    csv_file.parent.mkdir(parents=True, exist_ok=True)
    with csv_file.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    # Resumen.
    n = len(rows_out)
    n_oc = sum(1 for r in rows_out if r["oc"])
    n_match = sum(1 for r in rows_out if r["oc_match"] == "OK")
    n_diff = sum(1 for r in rows_out if r["oc_match"].startswith("DIFF"))
    log.w("\n" + "=" * 72)
    log.w("RESUMEN")
    log.w(f"  PDFs procesados:                 {n}")
    log.w(f"  Con OC extraído por bandas X:    {n_oc}  ({100*n_oc/n:.0f}%)" if n else "")
    if db:
        log.w(f"  OC coincide con BD:              {n_match}")
        log.w(f"  OC difiere de BD (revisar):      {n_diff}")
    log.w(f"  CSV: {csv_file}")
    log.flush_to_file(log_file)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Prototipo extractor Cat.2A por bandas X (BL-DLA-2-XBAND).")
    ap.add_argument("--pdf-dir", type=str, default=None)
    ap.add_argument("--pdf", type=str, default=None, help="Un solo PDF.")
    ap.add_argument("--db", type=str, default=None, dest="db_path",
                    help="fondos.sqlite para comparar contra OC actual.")
    ap.add_argument("--csv", type=str, default=None, dest="csv_path")
    ap.add_argument("--log", type=str, default=None, dest="log_path")
    ap.add_argument("--project-root", type=str, default=None, dest="project_root")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    main(pdf_dir=a.pdf_dir, pdf=a.pdf, db_path=a.db_path, csv_path=a.csv_path,
         log_path=a.log_path, project_root=a.project_root, debug=a.debug)
