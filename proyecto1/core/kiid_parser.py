# -*- coding: utf-8 -*-
"""
core/kiid_parser.py
Parser KIID determinista  — v17

Cambios v17 (2026-04-XX):

  FEE-FLAG-1   Nueva lógica Fee_Known_Flag en PASO 10c:
               Tras extraer Entry_Fee_Pct, se asigna:
               - "ZERO_CONFIRMED" si entry_fee == 0.0 (KIID declara sin comisión)
               - "EXTRACTED"      si entry_fee > 0.0 (valor numérico encontrado)
               - "NOT_FOUND"      si entry_fee es None (no extraído)
               Resuelve la ambigüedad NULL = "no cobra" vs "no sé si cobra".
               Crítico para modelo de costes de rotación en P3.

  FEE-ZERO-1   _ENTRY_FEE_ZERO_RE: nuevo patrón que detecta declaración
               explícita de "sin comisión de entrada" en el KIID/DDF.
               Cuando está presente, _detect_entry_fee() devuelve 0.0
               (en lugar de None) para que Fee_Known_Flag = ZERO_CONFIRMED.
               Equivalente a _EXIT_FEE_ZERO_RE ya existente para salida.

  INIT-FLAG-1  _empty_result(): añadido "Fee_Known_Flag": None para
               inicialización correcta del dict de resultado.
               El COALESCE en sqlite_writer preservará el valor existente
               si el ciclo CACHED no vuelve a extraer.


Cambios v5 (2026-03-08):  Corrección de inconsistencias detectadas en auditoría
                           post-ejecución de v4 sobre 3204 fondos.

  DERIVATIVES (Derivatives_Usage):
  FIX-DERIV-2  ES_DERIVATIVES_NO: patrón \"sin derivados\" restringido con lookahead
               negativo \"(?!\\s+y\\s+t[eé]cnicas)\" para evitar falso NO en KIIDs
               Fidelity donde el PDF introduce salto de línea entre \"sin\" (fin de
               frase \"sin embargo\") y la cabecera de sección \"Derivados y técnicas:\".
               Corrige 4 fondos Fidelity (LU0056886558, LU0766124712, LU1731833304,
               LU1731833569) que tenían Derivatives_Usage=NO siendo incorrecto.

  REPLICATION METHOD (Replication_Method):
  FIX-REPL-3  Nuevo valor PASSIVE: añadidos patrones ES y EN para detectar fondos
               de réplica pasiva/indexada que actualmente devuelven NULL:
               - ES: \"gestión pasiva\", \"gestiona de forma pasiva\", \"error de
                 seguimiento\", \"replicar la rentabilidad del índice\",
                 \"seguimiento del índice\"
               - Fused OCR: \"gestionadeformapasiva\", \"gestionpasiva\",
                 \"errordeseseguimiento\", \"errordeseguimiento\"
               - EN: \"passively managed\", \"passive management\",
                 \"tracking error\", \"index tracking\"
               Captura ~54 ETF/Fondo Indexado actualmente con NULL.
  FIX-REPL-4  ES_REPLICATION_ACTIVE: añadido \"gestiona activamente\" (tercera
               persona presente, sin \"de forma\") que cubre el formato Deutsche
               \"el fondo se gestiona activamente\". Captura ~13 fondos Deutsche/DWS
               actualmente con NULL.

Cambios v4 (2026-03-08):  Optimización post-análisis de 3204 KIIDs resueltos.

  BENCHMARK (Benchmark_Declared — 47.5% → objetivo ~65%):
  FIX-BENCH-4  _BENCH_TERMINATORS: añadidos terminadores que eliminan contaminación
               de columna adyacente en layout Fidelity/JPMorgan:
               'flamenco', 'francés', 'alemán', 'italiano', 'ingresos', 'inversor',
               'acumula', 'ofrezcan', 'remuner', 'distribuc', 'asesoramiento',
               'canjear', 'partici', 'folleto', 'www\b', '[()]s*(els+)?«'.
               Corrije 49 benchmarks contaminados activos.
  FIX-BENCH-5  _BENCH_SUFFIXES: añadidos variantes sin espacios para texto fusionado
               (totalreturn, netreturn, grossreturn, nettotalreturn, (nr), -nr, -net)
               para capturar benchmarks en OCR fused.
  FIX-BENCH-6  L0 fused: añadido 'subfondo' y 'subfondos' a _end_markers de la
               capa L0, evitando contaminación "russell1000valueindex·subfondosdelFolleto".
               También añadido 'usodederivados' como marcador de fin de sección.
  FIX-BENCH-7  L1 nuevo patrón: "Índice(s) de referencia <BENCHMARK>" (formato
               tabular Fidelity) sin exigir ':' → +267 fondos estimados.
  FIX-BENCH-8  L1 nuevo patrón: "índice de referencia\n<BENCHMARK>" donde el índice
               viene en la línea siguiente (BlueBox, Franklin) → +138 fondos.
  FIX-BENCH-9  L2 nuevo patrón: "el fondo medirá/mide su rentabilidad con respecto
               al / por referencia al [índice] <BENCHMARK>" → +58 fondos (Franklin,
               BlackRock, Amundi).
  FIX-BENCH-10 _trim_benchmark: elimina sufijo ", un índice que no" y trunca al primer
               proveedor para evitar captura de frase completa como nombre de índice.
               También corta en 's+(' cuando el token tras el paréntesis es texto
               y no un sufijo válido (ej.: "(el «índice»)").
  FIX-BENCH-11 L0 fused: añadido 'índicedereferencia' como cuarto label fused
               (sin "delaclasedeacciones") para cubrir formatos JPMorgan cortos.

  FUND_CURRENCY (Fund_Currency — 15.2% → objetivo ~30%):
  FIX-CURR-2   Nueva ES pattern: "moneda base del Fondo/Subfondo es [el/la] <nombre>"
               donde el nombre puede ser "dólar estadounidense", "euro", "libra
               esterlina", etc. — +314 fondos con moneda en texto pero no capturada.
  FIX-CURR-3   Nueva ES pattern: "Divisa de referencia <nombre> (ISO)" formato tabular
               de BlackRock/Fidelity → +120 fondos.
  FIX-CURR-4   _normalize_currency: añadidas formas singulares y compuestas para
               "dólar estadounidense", "libra esterlina", "yen japonés", "corona sueca/
               noruega/danesa", "franco suizo".

  HEDGING (Hedging_Policy — 28.1%):
  FIX-HEDGE-2  ES_HEDGED: nuevo patrón "clase de acciones [está] cubierta" que captura
               el formato "clase de acciones cubierta" de Fidelity (sin 'está').
  FIX-HEDGE-3  Language=None fused: añadida verificación de "coberturadc" /
               "coberturadc" / "cubiertafrente" para detectar fondos cubiertos en
               texto plenamente fusionado.

Cambios v3 (2026-03-07): ver historial en backup kiid_parser_v3.py.
"""

from typing import Dict, Optional
import re
from datetime import date
try:
    from proyecto1.core.srri_text import extract_srri
except ImportError:
    from core.srri_text import extract_srri
#from proyecto1.core.srri_v4_geometric import SRRIV4Geometric
USE_V5 = True

try:
    if USE_V5:
        try:
            from proyecto1.core.srri_v5_geometric import SRRIV5Geometric as SRRIExtractor
        except ImportError:
            from core.srri_v5_geometric import SRRIV5Geometric as SRRIExtractor
    else:
        try:
            from proyecto1.core.srri_v4_geometric import SRRIV4Geometric as SRRIExtractor
        except ImportError:
            from core.srri_v4_geometric import SRRIV4Geometric as SRRIExtractor
    _HAS_SRRI_VISUAL = True
except ImportError as _e_import:
    # Fallback: si el módulo geométrico no está disponible, continuar sin visual
    _HAS_SRRI_VISUAL = False
    class SRRIExtractor:  # type: ignore
        def __init__(self, **_): pass
        def extract(self, _): return None




# --- dependencias visuales ---
try:
    import cv2
    import numpy as np
    from pdf2image import convert_from_bytes
    _HAS_OPENCV = True
except Exception:
    _HAS_OPENCV = False


# =================================================
# API pública
# =================================================

def parse_kiid_generic(
    kiid_text: str,
    pdf_bytes: Optional[bytes] = None,
    isin: Optional[str] = None,
    fund_name: Optional[str] = None,
    srri_visual_prev: Optional[int] = None,   # SRRI_Visual previo de fund_kiid_metadata
    srri_textual_prev: Optional[int] = None,  # SRRI_Textual previo de fund_kiid_metadata
) -> Dict[str, Optional[str]]:

    result = _empty_result()

    # -------------------------------------------------
    # PASO 1 — SRRI (Extractor unificado)
    # -------------------------------------------------

    # Patrones de extracción textual SRRI desde texto KIID/DDF
    # ── Patrones SRRI para extracción desde texto plano (modo CACHED) ────────
    # Unificados con srri_text.py (_SRRIScanner) para garantizar equivalencia.
    # Orden: L0 (máxima fiabilidad) → L1 (alta fiabilidad) → fallback
    _SRRI_TEXT_PATTERNS = [
        # ── L0: patrones declarativos inequívocos ─────────────────────────────
        # ES: "hemos clasificado este producto en la clase de riesgo N"
        re.compile(r"hemos\s+clasificado\s+este\s+(?:producto|fondo|subfondo)\s+en\s+la\s+(?:clase\s+de\s+riesgo|categor[ií]a)\s+([1-7])", re.I),
        # ES: "hemos clasificado este producto como N de 7" / "como N en una escala"
        re.compile(r"hemos\s+clasificado\s+este\s+(?:producto|fondo|subfondo)\s+como\s+([1-7])\s+(?:de\s+(?:una\s+escala\s+de\s+)?7|en\s+una\s+escala)", re.I),
        # ES: "hemos clasificado esta cartera/solución en la clase de riesgo N"
        re.compile(r"hemos\s+clasificado\s+esta\s+(?:cartera|soluci[oó]n)\s+en\s+la\s+clase\s+de\s+riesgo\s+([1-7])", re.I),
        # ES: "se ha asignado la clase/categoría de riesgo N"
        re.compile(r"se\s+ha\s+asignado\s+(?:la\s+clase|la\s+categor[ií]a)\s+de\s+riesgo\s+([1-7])", re.I),
        # ES: "está clasificado en el nivel/clase N de 7"
        re.compile(r"est[aá]\s+clasificad[ao]\s+en\s+el\s+(?:nivel|clase)\s+([1-7])\s+(?:de\s+7|en\s+una)", re.I),
        # ES: "clase de riesgo N en una escala de 7"
        re.compile(r"clase\s+de\s+riesgo\s+([1-7])\s+en\s+una\s+escala\s+de\s+7", re.I),
        # ES: "de riesgo N en una escala"
        re.compile(r"de\s+riesgo\s+([1-7])\s+en\s+una\s+escala", re.I),
        # ES: "la categoría de riesgo N indica"
        re.compile(r"la\s+categor[ií]a\s+de\s+riesgo\s+([1-7])\s+indica", re.I),
        # ES: "en una escala de 7, la categoría de riesgo N"
        re.compile(r"en\s+una\s+escala\s+de\s+7[,.]?\s+la\s+categor[ií]a\s+de\s+riesgo\s+([1-7])", re.I),
        # ES: "en el nivel de riesgo N en una escala"
        re.compile(r"en\s+el\s+nivel\s+de\s+riesgo\s+([1-7])\s+en\s+una\s+escala", re.I),
        # ES: "nivel de riesgo N de 7 / en una escala de 7"
        re.compile(r"nivel\s+de\s+riesgo\s+([1-7])\s+(?:de\s+7|en\s+una\s+escala\s+de\s+7)", re.I),
        # ES: "un riesgo de N en una escala de 7"
        re.compile(r"un\s+riesgo\s+de\s+([1-7])\s+en\s+una\s+escala\s+de\s+7", re.I),
        # ES: "categoría N en una escala de 7"
        re.compile(r"categor[ií]a\s+([1-7])\s+en\s+una\s+escala\s+de\s+7", re.I),
        # ES: "categoría N de 7" (SISF/Schroeder)
        re.compile(r"categor[ií]a\s+([1-7])\s+de\s+(?:una\s+escala\s+de\s+)?7", re.I),
        # ES: "en la clase N de 7"
        re.compile(r"en\s+la\s+clase\s+([1-7])\s+de\s+7[,\s]", re.I),
        # ES: "producto en el nivel N" (Amundi DDF)
        re.compile(r"producto\s+en\s+el\s+nivel\s+([1-7])\b", re.I),
        # ES OCR fusionado JPMorgan
        re.compile(r"hemosclasificado[a-z]+deriesgo([1-7])enunaescalade7", re.I),
        # EN: "classified this product as class N" / "as N out of 7"
        re.compile(r"classified\s+this\s+(?:product|fund)\s+(?:as\s+)?(?:risk\s+)?(?:class\s+)?([1-7])\s+(?:out\s+of\s+7|of\s+7)", re.I),
        re.compile(r"classified\s+this\s+(?:product|fund)\s+(?:in\s+)?(?:risk\s+)?class\s+([1-7])", re.I),
        # EN: "risk class N on a scale"
        re.compile(r"risk\s+class\s+([1-7])\s+(?:on\s+a\s+scale|out\s+of)", re.I),
        # FR: "ce produit a été classé N sur 7"
        re.compile(r"ce\s+(?:produit|fonds?)\s+a\s+[eé]t[eé]\s+class[eé]\s+([1-7])\s+sur\s+7", re.I),
        # FR: "nous avons classé ce produit en catégorie N"
        re.compile(r"nous\s+avons\s+class[eé]\s+ce\s+(?:produit|fonds?)\s+(?:en\s+)?(?:cat[eé]gorie\s+)?([1-7])", re.I),
        # FR: "en classe de risque N sur 7" / "classé N sur 7"
        re.compile(r"en\s+classe\s+de\s+risque\s+([1-7])\s+sur\s+7", re.I),
        re.compile(r"class[eé]\s+([1-7])\s+sur\s+7[,\s]", re.I),

        # ── L1: patrones de alta fiabilidad ───────────────────────────────────
        re.compile(r"indicador\s+sint[eé]tico\s+de\s+riesgo\s+(?:es|:)\s*([1-7])", re.I),
        re.compile(r"indicateur\s+synth[eé]tique\s+de\s+risque\s+(?:est|:)\s*([1-7])", re.I),
        re.compile(r"summary\s+risk\s+indicator\s+(?:is|:)\s*([1-7])", re.I),
        re.compile(r"\brisk\s+(?:class|category)\s+([1-7])\b", re.I),
        re.compile(r"cat[eé]gorie\s+([1-7])\s+sur\s+7", re.I),
        re.compile(r"cat[eé]gorie\s+de\s+risque\s+([1-7])\b", re.I),
        re.compile(r"risikoklasse\s+([1-7])\s+von\s+7", re.I),
        re.compile(r"risikoklasse\s+([1-7])\b", re.I),

        # ── Fallback: "clase de riesgo N" (comodín, última posición) ──────────
        re.compile(r"clase\s+de\s+riesgo\s+([1-7])", re.I),
    ]

    def _extract_srri_textual(text: str) -> Optional[int]:
        """
        Extrae SRRI desde texto plano (Raw_KIID_Text) en modo CACHED.
        Unificado con _SRRIScanner de srri_text.py — mismos patrones L0+L1.
        Aplica normalización básica de espacios antes de buscar.
        """
        if not text:
            return None
        # Normalización básica: colapsar espacios múltiples (sin eliminar saltos)
        t = re.sub(r"[ \t]+", " ", text)
        for pat in _SRRI_TEXT_PATTERNS:
            m = pat.search(t)
            if m:
                val = int(m.group(1))
                if 1 <= val <= 7:
                    return val
        return None


    if pdf_bytes:


        # 1️⃣ Extraer SRRI completo (v3 base consolidado)
        srri_info = extract_srri(pdf_bytes)

        # 2️⃣ Ejecutar extractor visual (v4/v5)
        # Envuelto en try/except: un fallo del extractor (Tesseract no disponible,
        # excepción OpenCV, etc.) no debe abortar el parseo — el textual sigue.
        srri_visual = None
        try:
            engine = SRRIExtractor(isin=isin)
            srri_visual = engine.extract(pdf_bytes)
        except Exception as _e_vis:
            # Loguear para diagnóstico pero no propagar
            srri_info["Inference_Trace"] = _append_trace(
                srri_info.get("Inference_Trace"),
                f"VISUAL_ERROR[{type(_e_vis).__name__}]"
            )


        # 3️⃣ Si v4 devuelve valor, sustituir SOLO el campo visual
        if srri_visual is not None:
            srri_info["SRRI_Visual"] = srri_visual
            srri_info["Inference_Trace"] = _append_trace(
                srri_info.get("Inference_Trace"),
                "VISUAL_GEOM"
            )

        srri_text = srri_info.get("SRRI_Textual")

        # ── Reglas de desempate visual vs textual ─────────────────────────────
        # El extractor visual puede detectar widgets incorrectos en DDF:
        #   A) "N meses/months" → período de mantenimiento, no SRRI
        #   B) Tabla de escenarios de rentabilidad → escala similar al widget SRRI
        # En ambos casos el textual L0 (patrón declarativo) es más fiable.

        # Visual=1 sospechoso (PRIIP v3 vectorial sistemático)
        _visual_is_suspect_1 = (srri_visual == 1 and srri_text is not None and srri_text > 1)

        # Visual >> Textual por ≥3 niveles: probable detección de widget incorrecto
        # (tabla escenarios o período mantenimiento)
        _visual_is_suspect_high = (
            srri_visual is not None and srri_text is not None
            and srri_visual - srri_text >= 3
        )

        # Visual coincide con período de mantenimiento en el texto
        # Patrón: dígito seguido de meses/months/años/years
        _holding_period_digits: set = set()
        if kiid_text:
            import re as _re
            for _pat in [
                r'([1-7])\s+mes(?:es)?',
                r'([1-7])\s+month(?:s)?',
                r'([1-7])\s+mois',
                r'([1-7])\s+a[ñn]o(?:s)?',
                r'([1-7])\s+year(?:s)?',
                r'([1-7])\s+jahr(?:e)?',
            ]:
                for _m in _re.finditer(_pat, kiid_text, _re.I):
                    _holding_period_digits.add(int(_m.group(1)))
        _visual_is_holding_period = (
            srri_visual is not None and srri_visual in _holding_period_digits
            and srri_text is not None and srri_visual != srri_text
        )

        # Textual confirmado por declaración L0 explícita en el texto
        # Cuando el texto declara el SRRI inequívocamente ("hemos clasificado
        # este producto en la clase de riesgo N"), el textual prevalece aunque
        # la diferencia con visual sea solo ±1 o ±2.
        # Resuelve: tabla de escenarios (V=6 T=4), PRIIP sistemático (V=2 T=4)
        _l0_patterns_check = [
            r"hemos\s+clasificado\s+este\s+(?:producto|fondo|subfondo)\s+(?:como\s+|en\s+(?:la\s+(?:clase|categor[ií]a)\s+(?:de\s+riesgo\s+)?|el\s+nivel\s+(?:de\s+riesgo\s+)?))(\d)",
            r"se\s+ha\s+asignado\s+(?:la\s+)?(?:clase|categor[ií]a)\s+de\s+riesgo\s+(\d)",
            r"(?:fund|product)\s+is\s+(?:in\s+)?risk\s+(?:class|category)\s+(\d)\s+(?:out\s+of|of)\s+7",
            r"we\s+have\s+classified\s+this\s+(?:product|fund)\s+(?:as\s+)?(?:class\s+)?(\d)\s+(?:out\s+of|of)\s+7",
            r"ce\s+(?:produit|fonds?)\s+a\s+[eé]t[eé]\s+class[eé]\s+(\d)\s+sur\s+7",
            r"est[aá]\s+clasificad[ao]\s+en\s+(?:el\s+)?(?:nivel|clase)\s+(\d)\s+de\s+7",
        ]
        _textual_is_l0_confirmed = False
        if kiid_text and srri_text is not None:
            _txt_norm = re.sub(r"[ \t]+", " ", kiid_text.lower())
            for _lp in _l0_patterns_check:
                _lm = re.search(_lp, _txt_norm)
                if _lm:
                    try:
                        if int(_lm.group(1)) == srri_text:
                            _textual_is_l0_confirmed = True
                            break
                    except (ValueError, IndexError):
                        pass

        # Cualquier condición de visual sospechoso → preferir textual
        _visual_is_suspect = (
            _visual_is_suspect_1 or
            _visual_is_suspect_high or
            _visual_is_holding_period or
            _textual_is_l0_confirmed
        )

        if srri_text and srri_text == srri_visual:
            srri_info["SRRI"] = srri_text
            srri_info["SRRI_Validation_Status"] = "MATCH"
            srri_info["SRRI_Quality_Flag"] = "HIGH"
        elif srri_text and srri_visual is None:
            srri_info["SRRI"] = srri_text
            srri_info["SRRI_Validation_Status"] = "TEXT_ONLY"
            srri_info["SRRI_Quality_Flag"] = "MEDIUM_TEXT"
        elif srri_text and _visual_is_suspect:
            # Visual sospechoso (widget incorrecto): confiar en textual declarativo
            srri_info["SRRI"] = srri_text
            srri_info["SRRI_Validation_Status"] = "TEXT_ONLY"
            srri_info["SRRI_Quality_Flag"] = "MEDIUM_TEXT"
            srri_info["Inference_Trace"] = _append_trace(
                srri_info.get("Inference_Trace"),
                f"VISUAL_SUSPECT[vis={srri_visual},text={srri_text}]"
            )
        elif srri_text:
            srri_info["SRRI"] = srri_text
            srri_info["SRRI_Validation_Status"] = "CONFLICT"
            srri_info["SRRI_Quality_Flag"] = "LOW_CONFLICT"
        elif srri_visual is not None:
            srri_info["SRRI"] = srri_visual
            srri_info["SRRI_Validation_Status"] = "VISUAL_ONLY"
            srri_info["SRRI_Quality_Flag"] = "MEDIUM_VISUAL"
        else:
            srri_info["SRRI"] = None
            srri_info["SRRI_Validation_Status"] = "NOT_AVAILABLE"
            srri_info["SRRI_Quality_Flag"] = "NONE"

        # 4️⃣ Devolver exactamente lo que extract_srri consolida
        result["SRRI"] = srri_info.get("SRRI")
        result["SRRI_Visual"] = srri_info.get("SRRI_Visual")
        result["SRRI_Textual"] = srri_info.get("SRRI_Textual")
        result["SRRI_Validation_Status"] = srri_info.get("SRRI_Validation_Status")
        result["Inference_Trace"] = srri_info.get("Inference_Trace")
        result["SRRI_Quality_Flag"] = srri_info.get("SRRI_Quality_Flag")

    else:
        # Sin PDF (modo caché) — extracción textual + auditoría vs SRRI_Visual previo
        srri_text = _extract_srri_textual(kiid_text) if kiid_text else None

        if srri_text is not None:
            result["SRRI_Textual"] = srri_text

            if srri_visual_prev is not None:
                # Comparar nuevo textual vs visual anterior
                if srri_text == srri_visual_prev:
                    # Confirmación cruzada — sube calidad
                    result["SRRI"]                  = srri_text
                    result["SRRI_Visual"]            = srri_visual_prev  # preservar
                    result["SRRI_Validation_Status"] = "MATCH"
                    result["SRRI_Quality_Flag"]      = "HIGH"
                    result["Inference_Trace"]        = "SRRI_TEXT_MATCH_VISUAL"
                else:
                    # Conflicto texto vs visual previo — textual prevalece, marcar conflicto
                    result["SRRI"]                  = srri_text
                    result["SRRI_Visual"]            = srri_visual_prev  # preservar para auditoría
                    result["SRRI_Validation_Status"] = "CONFLICT"
                    result["SRRI_Quality_Flag"]      = "LOW_CONFLICT"
                    result["Inference_Trace"]        = (
                        f"SRRI_TEXT_CONFLICT_VISUAL[text={srri_text},vis={srri_visual_prev}]"
                    )
            else:
                # Sin visual previo — TEXT_ONLY
                result["SRRI"]                  = srri_text
                result["SRRI_Validation_Status"] = "TEXT_ONLY"
                result["SRRI_Quality_Flag"]      = "MEDIUM_TEXT"
                result["Inference_Trace"]        = "SRRI_TEXT_ONLY"

        else:
            # Sin extracción textual desde Raw_KIID_Text.
            # Intentar recuperar usando SRRI_Textual previo (de la BD), que puede
            # haber sido extraído en un ciclo anterior vía PDF o Raw_KIID_Text.
            # Esto evita la inconsistencia VISUAL_ONLY + SRRI_Textual_poblado
            # causada por divergencia entre las dos fuentes de extracción textual.
            _t_recovered = srri_textual_prev  # puede ser None
            result["SRRI_Visual"] = srri_visual_prev   # preservar siempre
            if _t_recovered is not None:
                result["SRRI_Textual"] = _t_recovered
                if srri_visual_prev is not None:
                    if _t_recovered == srri_visual_prev:
                        result["SRRI"]                  = _t_recovered
                        result["SRRI_Validation_Status"] = "MATCH"
                        result["SRRI_Quality_Flag"]      = "HIGH"
                        result["Inference_Trace"]        = "SRRI_TEXT_MATCH_VISUAL|TEXTUAL_RECOVERED"
                    else:
                        result["SRRI"]                  = _t_recovered
                        result["SRRI_Validation_Status"] = "CONFLICT"
                        result["SRRI_Quality_Flag"]      = "LOW_CONFLICT"
                        result["Inference_Trace"]        = (
                            f"SRRI_TEXT_CONFLICT_VISUAL[text={_t_recovered},vis={srri_visual_prev}]"
                            "|TEXTUAL_RECOVERED"
                        )
                else:
                    result["SRRI"]                  = _t_recovered
                    result["SRRI_Validation_Status"] = "TEXT_ONLY"
                    result["SRRI_Quality_Flag"]      = "MEDIUM_TEXT"
                    result["Inference_Trace"]        = "SRRI_TEXT_ONLY|TEXTUAL_RECOVERED"
            else:
                # Sin textual en absoluto — solo visual o nada
                result["SRRI"]                   = srri_visual_prev
                result["SRRI_Textual"]            = None  # explícito: no hay textual
                result["SRRI_Quality_Flag"]       = "NONE" if srri_visual_prev is None else "MEDIUM_VISUAL"
                result["SRRI_Validation_Status"]  = "NOT_AVAILABLE" if srri_visual_prev is None else "VISUAL_ONLY"
                result["Inference_Trace"]         = "SRRI_NOT_EXTRACTABLE"


    # -------------------------------------------------
    # PASO 2 — Language detection (textual)
    # -------------------------------------------------

    lang_info = _detect_language_deterministic(kiid_text)
    if lang_info:
        result["Language"] = lang_info["value"]
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"LANG_TEXT[{lang_info['value']}]"
        )

    # -------------------------------------------------
    # PASO 3 — KIID_Published_Date
    # -------------------------------------------------

    date_info = _extract_kiid_published_date(
        kiid_text,
        result.get("Language")
    )

    if date_info is not None:
        result["KIID_Published_Date"] = date_info
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"KIID_DATE_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 4 — Derivatives_Usage
    # -------------------------------------------------

    val = _detect_derivatives_usage(kiid_text, result.get("Language"))
    if val is not None:
        result["Derivatives_Usage"] = val
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"DERIVATIVES_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 5 — Benchmark_Detection
    # -------------------------------------------------

    bench = _detect_benchmark_declared(kiid_text, result.get("Language"))
    if bench:
        result["Benchmark_Declared"] = bench
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            "BENCHMARK_TEXT[ES]"
        )

    # -------------------------------------------------
    # PASO 6 — Replication_Method
    # -------------------------------------------------

    repl = _detect_replication_method(kiid_text, result.get("Language"))
    if repl:
        result["Replication_Method"] = repl
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"REPLICATION_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 7 — Hedging_Policy
    # -------------------------------------------------

    hedge = _detect_hedging_policy(kiid_text, result.get("Language"))
    if hedge:
        result["Hedging_Policy"] = hedge
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"HEDGING_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 8 — Fund_Currency
    # -------------------------------------------------

    curr = _detect_fund_currency(kiid_text, result.get("Language"))
    if curr:
        result["Fund_Currency"] = curr
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"CURRENCY_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 9 — Portfolio_Currency
    # -------------------------------------------------

    pcur = _detect_portfolio_currency(kiid_text, result.get("Language"))
    if pcur:
        result["Portfolio_Currency"] = pcur
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"PORTFOLIO_CURRENCY_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 9b — Fallbacks desde nombre del fondo
    # Se aplican solo si el parsing de texto no tuvo éxito.
    # -------------------------------------------------
    if fund_name:
        name_up = fund_name.upper()

        # Fund_Currency desde nombre: "... EUR ACC", "... USD INC", etc.
        if not result.get("Fund_Currency"):
            _CURR_IN_NAME = re.compile(
                r'\b(EUR|USD|GBP|JPY|CHF|SEK|NOK|DKK|AUD|CAD|PLN|CZK|HUF)\b'
            )
            m_name_curr = _CURR_IN_NAME.search(name_up)
            if m_name_curr:
                result["Fund_Currency"] = m_name_curr.group(1)
                result["Inference_Trace"] = _append_trace(
                    result["Inference_Trace"],
                    "CURRENCY_FROM_NAME"
                )

        # Hedging_Policy desde nombre: H, HGD, HEDG, HEDGED, (H), HGDB
        if not result.get("Hedging_Policy"):
            _HEDGE_IN_NAME = re.compile(
                r'\b(?:HGD[B]?|HEDG(?:ED)?)\b|\(H\)|\bH\s+(?:ACC|INC|DIST|EUR|USD|GBP)',
                re.IGNORECASE
            )
            if _HEDGE_IN_NAME.search(name_up):
                result["Hedging_Policy"] = "HEDGED"
                result["Inference_Trace"] = _append_trace(
                    result["Inference_Trace"],
                    "HEDGING_FROM_NAME"
                )

    # -------------------------------------------------
    # PASO 10 — Ongoing_Charge (gastos corrientes)
    # -------------------------------------------------

    oc = _detect_ongoing_charge(kiid_text, result.get("Language"))
    if oc is not None:
        result["Ongoing_Charge"] = oc
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"ONGOING_CHARGE_TEXT[{result.get('Language')}]"
        )

    # -------------------------------------------------
    # PASO 10c — Entry_Fee_Pct + Fee_Known_Flag (v17)
    # -------------------------------------------------
    entry_fee = _detect_entry_fee(kiid_text)
    if entry_fee is not None:
        result["Entry_Fee_Pct"] = entry_fee
        result["Fee_Known_Flag"] = "ZERO_CONFIRMED" if entry_fee == 0.0 else "EXTRACTED"
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"ENTRY_FEE[{entry_fee:.4f}][{result['Fee_Known_Flag']}]"
        )
    else:
        result["Fee_Known_Flag"] = "NOT_FOUND"

    # -------------------------------------------------
    # PASO 10d — Exit_Fee_Pct (comisión de salida)
    # -------------------------------------------------
    exit_fee = _detect_exit_fee(kiid_text)
    if exit_fee is not None:
        result["Exit_Fee_Pct"] = exit_fee
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"EXIT_FEE[{exit_fee:.4f}]"
        )

    # -------------------------------------------------
    # PASO 10e — SFDR Article
    # -------------------------------------------------
    sfdr = _detect_sfdr_article(kiid_text)
    if sfdr is not None:
        result["Sfdr_Article"] = sfdr
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"SFDR[{sfdr}]"
        )

    # -------------------------------------------------
    # PASO 10f — Recommended_Holding_Period
    # -------------------------------------------------
    rhp = _detect_recommended_holding_period(kiid_text)
    if rhp:
        result["Recommended_Holding_Period"] = rhp
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"RHP[{rhp}]"
        )

    # -------------------------------------------------
    # PASO 10g — Leverage_Used
    # -------------------------------------------------
    lev = _detect_leverage(kiid_text)
    if lev:
        result["Leverage_Used"] = lev
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"LEVERAGE[{lev}]"
        )

    # -------------------------------------------------
    # PASO 10h — Liquidity_Profile
    # -------------------------------------------------
    liq = _detect_liquidity_profile(kiid_text)
    if liq:
        result["Liquidity_Profile"] = liq
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"LIQUIDITY[{liq}]"
        )

    # -------------------------------------------------
    # PASO 10b — Accumulation_Policy (acumulación / distribución)
    # -------------------------------------------------
    accum = _detect_accumulation_policy(kiid_text, result.get("Language"))
    if accum:
        result["Accumulation_Policy"] = accum
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"ACCUM_POLICY[{accum}]"
        )

    # -------------------------------------------------
    # PASO 10i — Distribution_Frequency
    # -------------------------------------------------
    dist_freq = _detect_distribution_frequency(
        kiid_text, result.get("Accumulation_Policy")
    )
    if dist_freq:
        result["Distribution_Frequency"] = dist_freq
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"DIST_FREQ[{dist_freq}]"
        )

    return result


# =================================================
# Helpers comunes
# =================================================

def _empty_result() -> Dict[str, Optional[str]]:
    return {
        "SRRI":                   None,
        "SRRI_Visual":            None,
        "SRRI_Textual":           None,
        "SRRI_Validation_Status": None,
        "SRRI_Quality_Flag":      None,
        "Language":               None,
        "KIID_Published_Date":    None,
        "Derivatives_Usage":      None,
        "Benchmark_Declared":     None,
        "Replication_Method":     None,
        "Hedging_Policy":         None,
        "Fund_Currency":          None,
        "Portfolio_Currency":     None,
        "Ongoing_Charge":         None,
        "Entry_Fee_Pct":          None,   # Comisión de entrada decimal (0.045 = 4.5%)
        "Exit_Fee_Pct":           None,   # Comisión de salida decimal (0.005 = 0.5%)
        "Sfdr_Article":           None,   # 6 | 8 | 9 (SFDR regulation article)
        "Recommended_Holding_Period": None, # ej. "1D-3M" | "1Y" | "3Y" | "5Y" | "10Y+"
        "Leverage_Used":          None,   # YES | NO | LIMITED
        "Liquidity_Profile":      None,   # T0 | T1 | T2 | T5 | T10+ (días hábiles rescate)
        "Distribution_Frequency": None,   # MONTHLY | QUARTERLY | ANNUAL | VARIABLE
        "Accumulation_Policy":    None,   # ACCUMULATION / DISTRIBUTION
        "Fee_Known_Flag":         None,   # v17: EXTRACTED | ZERO_CONFIRMED | NOT_FOUND
        "Inference_Trace":        None,
    }
    return {
        "SRRI": None,
        "SRRI_Visual": None,
        "SRRI_Textual": None,

        "Fund_Currency": None,
        "Hedging_Policy": None,
        "Replication_Method": None,
        "Derivatives_Usage": None,
        "Benchmark_Declared": None,
        "Language": None,
        "KIID_Published_Date": None,
        "Inference_Trace": None,
    }


def _append_trace(existing: Optional[str], new: str) -> str:
    return f"{existing}|{new}" if existing else new


# =================================================
# Language detection (DETERMINISTA)
# =================================================

_LANG_KEYWORDS = {
    "ES": [
        "este documento", "el fondo", "perfil de riesgo",
        "rentabilidad", "indicador sintético"
    ],
    "EN": [
        "this document", "the fund", "risk profile",
        "returns", "synthetic risk indicator"
    ],
    "FR": [
        "ce document", "le fonds", "profil de risque"
    ],
    "DE": [
        "dieses dokument", "der fonds", "risikoprofil"
    ],
    "IT": [
        "questo documento", "il fondo", "profilo di rischio"
    ],
}

# Palabras clave sobre texto fusionado (sin espacios) para OCR JPMorgan y similares
_LANG_KEYWORDS_FUSED = {
    "ES": [
        "documentodedatos",          # "documento de datos [fundamentales]"
        "informacionfundamental",    # normalizado sin acento
        "perfilderiesgo",
        "elfondo",
        "rentabilidad",
        "productodeinversion",       # "producto de inversión" normalizado
    ],
    "EN": [
        "keyinformation",
        "fundamentalinformation",
        "riskprofile",
        "thefund",
    ],
}


def _strip_accents_fused(s: str) -> str:
    """Elimina diacríticos y espacios/newlines para comparación OCR fusionado."""
    import unicodedata
    no_acc = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if not unicodedata.combining(c)
    )
    return no_acc.replace(" ", "").replace("\n", "").replace("\r", "")


def _detect_language_deterministic(text: str) -> Optional[Dict[str, str]]:
    if not text or len(text) < 50:
        return None

    text_l = text.lower()

    # ── Paso 1: detección normal con espacios ────────────────────────────────
    for lang, keywords in _LANG_KEYWORDS.items():
        hits = [k for k in keywords if k in text_l]
        if len(hits) >= 2:
            return {"value": lang, "evidence": hits}

    # ── Paso 2: detección sobre texto fusionado (OCR JPMorgan y similares) ──
    # Se normaliza quitando acentos, espacios Y saltos de línea.
    # Los 207 fondos JPMorgan tienen el texto completamente concatenado;
    # los keywords con espacios nunca hacen match en el paso 1.
    t_fused = _strip_accents_fused(text_l)
    for lang, keywords in _LANG_KEYWORDS_FUSED.items():
        hits = [k for k in keywords if k in t_fused]
        if len(hits) >= 2:
            return {"value": lang, "evidence": hits, "source": "fused"}

    return None


# =================================================
# Elimina ruido OCR
# =================================================


def _normalize_ocr_noise(text: str) -> str:
    """
    Normaliza ruido típico de OCR:
    - 'r ussell'  -> 'russell'
    - 'm sci'     -> 'msci'
    - 's & p'     -> 's&p'
    - 's &p'      -> 's&p'
    """
    t = text

    # colapsar espacios múltiples
    t = re.sub(r"\s+", " ", t)

    # unir letras separadas artificialmente (solo secuencias cortas)
    t = re.sub(r"\b([a-z])\s+([a-z])\b", r"\1\2", t)

    # normalizaciones específicas conocidas
    t = t.replace("s & p", "s&p")
    t = t.replace("s &p", "s&p")
    t = t.replace("s& p", "s&p")

    return t.strip()


# =================================================
# KIID Published Date (DETERMINISTA)
# =================================================

_ES_MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11,
    "december": 12,
}

def _extract_kiid_published_date(
    text: Optional[str],
    language: Optional[str]
) -> Optional[str]:

    if not text or not language:
        return None

    text = (
    text.lower()
        .replace("\n", " ")
        .replace("\u00a0", " ")
    )


    # ---------- ESPAÑOL ----------

    if language == "ES":
        t = text

        # dd/mm/yyyy cerca de "documento"
        m = re.search(
            r"(documento|publicad|publicaci|publicó|válid|actualiz)[^.]{0,80}?(\d{1,2})/(\d{1,2})/(\d{4})",
            t
        )
        if m:
            day = m.group(2)
            month = m.group(3)
            year = m.group(4)
            return _safe_date(year, month, day)

        # "15 de marzo de 2022" cerca de "documento"
        m = re.search(
            r"(documento|publicad|publicado|publicó|válid|actualiz)[^.]{0,80}?(\d{1,2}) de ([a-z]+) de (\d{4})",
            t
        )
        if m and m.group(3) in _ES_MONTHS:
            day = m.group(2)
            month = _ES_MONTHS[m.group(3)]


        # Caso 3: "Este documento se publicó el 27/02/2025"
        # Caso: "Este documento se publicó el 27/02/2025"
        m = re.search(
            r"este\s+documento\s+se\s+public[oó]\s+el\s+(\d{1,2})/(\d{1,2})/(\d{4})",
            text
        )
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)

            return _safe_date(year, month, day)


    # ---------- INGLÉS ----------
    if language == "EN":
        # Date of publication: 15/03/2022
        m = re.search(
            r"date of publication[:\s]+(\d{2})/(\d{2})/(\d{4})",
            text
        )
        if m:
            return _safe_date(m.group(3), m.group(2), m.group(1))

        # published on 15 march 2022
        m = re.search(
            r"published on (\d{1,2}) ([a-z]+) (\d{4})",
            text
        )
        if m and m.group(2) in _EN_MONTHS:
            return _safe_date(
                m.group(3),
                _EN_MONTHS[m.group(2)],
                m.group(1)
            )

    return None


def _safe_date(year, month, day) -> Optional[str]:
    try:
        d = date(int(year), int(month), int(day))
        return d.isoformat()
    except Exception:
        return None




# =================================================
# DERIVATIVE USAGE  (v2 — mejorado)
# =================================================
# Análisis empírico sobre 848 KIIDs:
#   - 250 ya capturados como YES
#   - 0 capturados como NO  (el texto nunca dice "no se utilizan derivados"
#     explícitamente; el patrón es implícito o está en otra sección)
#   - 598 sin dato → 161 nuevos YES identificables
#
# Criterio de diseño:
#   Prioridad NO > YES para evitar falsos positivos.
#   Si el texto dice "el fondo NO utiliza derivados", se impone.
#   Si hay swaps/futuros/opciones nombrados, es YES.
# -------------------------------------------------

ES_DERIVATIVES_NO = [
    r"\bno\s+utiliza[r]?\s+(?:instrumentos\s+)?derivados\b",
    r"\bno\s+se\s+utilizar[aá]n?\s+(?:instrumentos\s+)?derivados\b",
    r"\bno\s+(?:utiliza|emplea|usa)\s+(?:instrumentos\s+)?derivados\b",
    r"\bno\s+se\s+(?:utilizar[aá]n|emplear[aá]n|usar[aá]n)\s+(?:instrumentos\s+)?derivados\b",
    r"\bno\s+invierte\s+en\s+derivados\b",
    r"\bno\s+hace\s+uso\s+de\s+(?:instrumentos\s+)?derivados\b",
    r"\bno\s+incorpora\s+derivados\b",
    # FIX-DERIV-2: lookahead negativo para "sin\nDerivados y técnicas:" (Fidelity PDF layout)
    r"\bsin\s+(?:uso\s+de\s+)?derivados\b(?!\s+y\s+t[eé]cnicas)",
]

ES_DERIVATIVES_YES = [
    # Explícitos: usa/utiliza/emplea/puede usar
    r"\b(?:puede|podr[aá]|podrán)\s+(?:utilizar|emplear|usar)\s+(?:instrumentos\s+)?derivados\b",
    r"\b(?:utiliza|utilizar[aá]|emplea|usa)\s+(?:instrumentos\s+)?derivados\b",
    r"\bhace\s+uso\s+de\s+(?:instrumentos\s+)?derivados\b",
    r"\bderivados\s+con\s+fines\s+de\s+(?:cobertura|inversi[oó]n)\b",
    r"\buso\s+(?:limitado|moderado)?\s*de\s+(?:instrumentos\s+)?derivados\b",
    # Tabla "uso de derivados" con contexto posterior
    r"\buso\s+de\s+derivados[,:\s]+(?:[^\n\.]{0,120})(?:cobertura|inversi[oó]n|gesti[oó]n|especulaci[oó]n|protecci[oó]n)\b",
    # Instrumentos derivados (genérico sin calificador NO)
    r"\binstrumentos\s+derivados\b",
    # Tipos concretos de derivados nombrados
    r"\b(?:swaps?|opciones?|contratos?\s+de\s+futuros?|forwards?|warrants?|cfds?|permutas?\s+financieras?)\b",
    r"\b(?:interest\s+rate\s+swap|credit\s+default\s+swap|total\s+return\s+swap)\b",
    # Cobertura de divisa mediante derivados
    r"\bcobertura\s+(?:de\s+divisa|cambiaria|de\s+tipo\s+de\s+cambio)\s+(?:mediante|a\s+través\s+de)\s+(?:instrumentos\s+)?derivados\b",
]

EN_DERIVATIVES_NO = [
    r"\bdoes\s+not\s+use\s+(?:financial\s+)?derivatives\b",
    r"\bwill\s+not\s+use\s+(?:financial\s+)?derivatives\b",
    r"\bdoes\s+not\s+invest\s+in\s+(?:financial\s+)?derivatives\b",
]

EN_DERIVATIVES_YES = [
    r"\bmay\s+use\s+(?:financial\s+)?derivatives\b",
    r"\buses\s+(?:financial\s+)?derivatives\b",
    r"\bemploys?\s+(?:financial\s+)?derivatives\b",
    r"\b(?:swaps?|options?|futures?|forwards?|warrants?)\b",
]


def _detect_derivatives_usage(text: str, language: Optional[str]) -> Optional[str]:
    if not text:
        return None

    t = text.lower()
    t_nospace = t.replace(" ", "")

    # ── Texto OCR fusionado: "derivadosuso:" ─────────────────────────────────
    # 103 fondos con Language=None tienen "derivadosuso:gestión eficaz..." o
    # "derivadosuso:cobertura..." en su texto fusionado.
    if language is None:
        if any(p in t_nospace for p in ["noderivados", "noderivado"]):
            return "NO"
        if any(p in t_nospace for p in ["derivadosuso:", "instrumentosderivados",
                                          "usodederivados", "derivadosuso"]):
            return "YES"
        return None

    if language in ("ES", None):
        # NO tiene prioridad
        for rx in ES_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        for rx in ES_DERIVATIVES_YES:
            if re.search(rx, t):
                return "YES"
        if "instrumentosderivados" in t_nospace or "usodederivados" in t_nospace:
            return "YES"

    if language == "EN":
        for rx in EN_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        for rx in EN_DERIVATIVES_YES:
            if re.search(rx, t):
                return "YES"

    return None


# =================================================
# BENCHMARK_DECLARED  (v2 — reescrito)
# =================================================
# Diseño basado en análisis empírico:
#
#   Patrón actual: busca cualquier secuencia que termine en "index"
#   → sólo captura el 35.8% (304/848)
#
#   Causas de los gaps:
#   1. Guardrail "endswith index" demasiado estricto:
#      excluye "MSCI Europe (Net Return)", "Bloomberg Global Aggregate",
#      "Russell 1000 Value Net TR", "€STR Capitalized", etc.
#   2. Patrón no anclado a triggers contextuales → mucho ruido y falsos positivos
#   3. No cubre "valor de referencia: MSCI ..."
#   4. No cubre benchmark en nombre del producto ("EUR (Hedged)" → HEDGED, no benchmark)
#   5. Texto OCR fusionado ("índicemscibworld..." sin espacios)
#
#   Nuevo diseño:
#   - Tres capas de triggers con prioridad decreciente:
#       L1: Triggers contextuales fuertes (índice de referencia:, valor de referencia:)
#       L2: Triggers de acción (superar, comparar, replicar)
#       L3: Reconocimiento directo del proveedor en posición cualificada
#   - Sufijos válidos: index, índice, net return, net tr, total return, (nr), (net),
#     capitalized, compounded, gross return, net div reinvested
#   - Post-trim: corta en primer terminador semántico
#   - Normalización de ruido OCR
#   - Devuelve None para "no tiene ningún valor de referencia"
# -------------------------------------------------

# Proveedores de índices conocidos
_BENCH_PROVIDERS = (
    r"msci|bloomberg|barclays|ftse|russell|s&p|stoxx|euro\s*stoxx|eurostoxx|nasdaq|"
    r"dow\s+jones|nikkei|topix|hang\s+seng|dax|cac|ibex|omx|tsx|asx|"
    r"iboxx|\bice\b|bofa(?:ml)?|merrill\s+lynch|jp\s+morgan|jpmorgan|solactive|"
    r"morningstar|markit|itraxx|cdx|korea|kospi|sensex|nifty|bse|"
    r"€str|\bestr\b|euribor|libor|\bsofr\b|sonia|tona|tonar"
)

# Sufijos que confirman que lo capturado es un índice/benchmark
# v4: añadidos variantes sin espacio (texto OCR fusionado) y sufijos abreviados
_BENCH_SUFFIXES = (
    r"index|índice|net\s+(?:tr|return)|total\s+return|nr\b|-nr\b|-net\b|gross\s+return|"
    r"net\s+div(?:idend)?\s+reinvested|capitaliz[ae]d|compounded|"
    r"\(net\)|\(nr\)|\(total\s+return\)|"
    # variantes fusionadas (sin espacios):
    r"totalreturn|netreturn|grossreturn|nettotalreturn|netdividendreinvested"
)

# Terminadores: indican el fin del nombre del benchmark
# v4: añadidos terminadores de columna adyacente (Fidelity/Franklin/BlackRock)
_BENCH_TERMINATORS = re.compile(
    r"\s+(?:el|la|se|del|de\s+la|en|que|y\s+(?:el|la)|cobertura|consúltese|para|"
    r"usos|uso|derivados|q\s+|apartado|exclusiones|con\s+fines|método\s+de|"
    r"gestionar|limitaciones|defensivos|indicativo|previsto|solamente|"
    # v4 añadidos: contaminaciones de columna (idiomas, distribución, inversor...)
    r"flamenco|franc[eé]s|alem[aá]n|italiano|español|ingresos|inversor|"
    r"acumula|ofrezcan|remuner|distribuc|asesoramiento|canjear|partici|folleto|"
    r"anual|trimestral|semestral|por\s+lo|informaci|consult|precio|clase\b|"
    r"[a-z]{15,})|"
    r"[\.;\n]|"
    r"\s{2,}|"
    r"\bwww\b|"                         # URLs sin paréntesis
    r"\s+\(www|"                        # URLs con paréntesis
    r",\s+un\s+(?:índice|index)|"       # ", un índice que no..." al final
    r"\s+\([^)]{0,6}\)\s+(?:el|la|se|un|una|para)\b|"  # parenthesis + article = adjacent text
    r"\s+\(el\s|\s+\(la\s|\s+\(un\s"  # "(el ...) texto adjunto"
)

# Frases que indican "sin benchmark" → devolver NO_BENCHMARK sentinel
#
# Distincion critica:
#   NULL           = el parser no encontro nada (incertidumbre)
#   "NO_BENCHMARK" = el KIID declara explicitamente que no sigue ningun indice
#                    → gestion activa pura confirmada
_NO_BENCH_PHRASES = [
    # Patrones originales
    r"no\s+tiene\s+ningún\s+valor\s+de\s+referencia",
    r"se\s+gestiona\s+sin\s+(?:utilizar\s+(?:un\s+)?)?índice\s+de\s+referencia",
    r"sin\s+índice\s+de\s+referencia",
    r"no\s+está\s+gestionado\s+con\s+referencia\s+a\s+ningún\s+índice",
    r"gestión\s+activa\s+y\s+no\s+tiene\s+ningún\s+valor\s+de\s+referencia",
    # Goldman Sachs: "no toma como referencia ningún valor"
    r"no\s+toma\s+como\s+referencia\s+ning[uú]n\s+valor",
    # MorganStanley / varios: "la rentabilidad del fondo no se compara con"
    r"rentabilidad\s+del\s+fondo\s+no\s+se\s+compara",
    # Invesco / varios: "no está limitado por ninguno" / "no está referenciado"
    r"(?:no|ni)\s+está\s+(?:limitado|referenciado)\s+por\s+ning",
    # "no sigue ningún índice" / "sin referencia a ningún índice"
    r"no\s+sigue\s+ning[uú]n\s+[íi]ndice",
    r"sin\s+referencia\s+a\s+ning[uú]n\s+[íi]ndice",
    # Gestión activa sin referencia (varias gestoras)
    r"gestionado\s+activamente\s+(?:y\s+)?(?:sin|no)",
    r"no\s+pretende\s+(?:replicar|seguir)\s+ning[uú]n\s+(?:[íi]ndice|valor)",
    # Inglés (fondos con KIID en EN)
    r"not\s+managed\s+(?:with\s+reference|in\s+relation)\s+to\s+(?:any|an?)\s+(?:index|benchmark)",
    r"does\s+not\s+track\s+(?:any|an?)\s+(?:index|benchmark)",
    r"no\s+benchmark",
]

_NO_BENCH_RE = re.compile("|".join(_NO_BENCH_PHRASES), re.IGNORECASE)


def _trim_benchmark(raw: str) -> Optional[str]:
    """
    Limpia el texto capturado tras el trigger:
    - Elimina texto de relleno al principio (hasta el primer proveedor)
    - Corta en el primer terminador semántico
    - Normaliza ruido OCR
    - Verifica mínimo de calidad
    """
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = _normalize_ocr_noise(raw)

    # Eliminar "(el «valor de referencia»)" y similares al inicio
    raw = re.sub(r"^\(?el\s+[«\"]?valor\s+de\s+referencia[»\"]\)?\s*", "", raw)

    # Encontrar posición del proveedor en el texto
    prov_m = re.search(_BENCH_PROVIDERS, raw)
    if not prov_m:
        return None
    raw = raw[prov_m.start():]

    # Cortar en terminadores
    term_m = _BENCH_TERMINATORS.search(raw)
    if term_m and term_m.start() >= 3:
        raw = raw[:term_m.start()].strip()

    # Eliminar paréntesis sueltos al final y coma/punto sobrante
    raw = re.sub(r"\s*[\(\[]\s*$", "", raw).strip()
    raw = re.sub(r"\s*[®™©]\s*", " ", raw).strip()
    raw = re.sub(r"[,;]\s*$", "", raw).strip()  # v4: trailing comma/semicolon

    # Eliminar sufijo "(total..." incompleto al final (contaminación columna adyacente)
    # "msci europe index (total método de cálculo" → "msci europe index"
    raw = re.sub(r'\s*\(total(?!\s+(?:return|net|gross|tr))(?![a-z])[^)]{0,60}$', '', raw, flags=re.IGNORECASE).strip()

    if len(raw) < 6:
        return None
    # Rechazar resultado de una sola palabra sin sufijo (p.ej. 'jpmorgan' solo)
    if ' ' not in raw and not re.search(_BENCH_SUFFIXES, raw):
        return None

    # v4: Rechazar resultados que contienen términos de gestora (falsos positivos)
    _FALSE_POSITIVE_TERMS = re.compile(
        r"asset\s+management|gestoras?|depositario|sociedad\s+gestora|"
        r"administrad|domiciliado|registrad|subgestor",
        re.IGNORECASE
    )
    if _FALSE_POSITIVE_TERMS.search(raw):
        return None

    # Verificar que contiene un sufijo de índice válido O termina con el proveedor
    has_suffix = bool(re.search(_BENCH_SUFFIXES, raw))
    has_provider = bool(re.search(_BENCH_PROVIDERS, raw))
    if not has_provider:
        return None

    return raw[:120]


# Triggers L1 — contexto fuerte, captura el resto de la línea/cláusula
# v4: añadidos "Índice(s) de referencia" (Fidelity) y "índice de referencia\n" (BlueBox/Franklin)
_L1_PATTERNS = [
    # "Índice(s) de referencia [de la clase de acciones]: <benchmark>" — CON separador
    # Sin colon/dash la frase es incidental ("el índice de referencia bajo circunstancias")
    r"índice\s+de\s+referencia\s*(?:de\s+la\s+clase\s+de\s+(?:acciones|participaciones)\s*)?[:\-]\s*([^\n]{10,130})",
    # "valor de referencia: <benchmark>"
    r"valor\s+de\s+referencia\s*:\s*(?:índice\s+)?([^\n]{8,130})",
    # "benchmark: [índice] <benchmark>"
    r"benchmark\s*:\s*(?:índice\s+)?([^\n]{8,120})",
    # v4 NUEVO: "Índice(s) de referencia <BENCHMARK>" — formato tabular Fidelity (sin ':')
    # Solo si lo que sigue contiene un proveedor conocido (filtrará _trim_benchmark)
    r"índice\(?s?\)?\s+de\s+referencia\s+(?!de\s+la\s+clase)([^\n]{8,130})",
    # v4 NUEVO: "índice de referencia\n<BENCHMARK>" — benchmark en línea siguiente (BlueBox/Franklin)
    r"índice\s+de\s+referencia\s*\n\s*([^\n]{8,130})",
    # DDF NUEVO: "Índice de referencia:" con texto intermedio largo (DDF Amundi/Deutsche/Fidelity)
    # El benchmark aparece tras descripción de gestión (hasta 250 chars después del trigger)
    r"índice\s+de\s+referencia\s*:[^\n]{0,250}?([^\n]{8,100})",
]

# Triggers L2 — acción del gestor
# v4: añadido "el fondo medirá/mide su rentabilidad con/por referencia al [índice]"
_L2_PATTERNS = [
    # "superar/batir [al/el] [índice] <benchmark>"
    r"(?:superar|batir|supere)\s+(?:al?\s+|el\s+|a\s+la\s+rentabilidad\s+del\s+)?(?:índice\s+)?([^\n\.;]{10,120})",
    # "comparar la rentabilidad con [el índice] <benchmark>"
    r"compar[aá]r?\w*\s+la\s+rentabilidad[^\n\.;]{0,30}?(?:índice\s+|con\s+(?:el\s+)?(?:índice\s+)?)([^\n\.;]{10,100})",
    # "replicar [el] [índice] <benchmark>"
    r"replica[r]?\s+(?:(?:el|la)\s+)?(?:índice\s+)?([^\n\.;]{10,100})",
    # "obtener una rentabilidad similar a [la del índice] <benchmark>"
    r"rentabilidad\s+similar\s+a\s+(?:la\s+del\s+índice\s+)?([^\n\.;]{10,100})",
    # v4 NUEVO: "el fondo medirá/mide su rentabilidad con respecto al/por referencia al <benchmark>"
    r"(?:el\s+fondo|la\s+cartera)\s+(?:medirá|mide|medir[aá]|mide|medira)\s+su\s+rentabilidad\s+(?:con\s+respecto\s+al?|por\s+referencia\s+al?)\s+(?:índice\s+)?([^\n\.;]{10,120})",
    # v4 NUEVO: "rentabilidad del fondo se comparará/medirá con respecto al <benchmark>"
    r"rentabilidad\s+del\s+fondo\s+se\s+(?:comparará|medirá|compara|mide)\s+(?:con\s+respecto\s+al?|frente\s+al?|con\s+)?(?:el\s+)?(?:índice\s+)?([^\n\.;]{10,120})",
]

# Triggers L3 — reconocimiento directo de proveedor en posición destacada
_L3_PATTERNS = [
    # "índice <PROVEEDOR> <nombre>"
    r"índice\s+(" + _BENCH_PROVIDERS + r"[^\n\.;]{3,100})",
    # "<PROVEEDOR> <nombre> [index/net return/...]"
    # NOTE: _BENCH_PROVIDERS se envuelve en (?:...) para evitar que la alternación
    # absorba el \s+[a-z]... que debe ser común a todos los proveedores.
    r"(?:^|[\s:,(])((?:" + _BENCH_PROVIDERS + r")\s+[a-z][a-z0-9&®\s\.\-\(\)/]{4,80}?" +
    r"(?:" + _BENCH_SUFFIXES + r"))",
    # "<PROVEEDOR> X se emplea para comparar/supervisar la rentabilidad"
    # JPMorgan KIID: "s&p 500 index se emplea para comparar la rentabilidad"
    r"((?:" + _BENCH_PROVIDERS + r")[a-z0-9&®\s\.\-\(\)/]{3,80}?)\s+se\s+emplea\s+para\s+(?:comparar|supervisar|medir|seguir)",
]


def _detect_benchmark_declared(text: str, language: Optional[str]) -> Optional[str]:
    """
    Detector de benchmark v2.
    Funciona sobre texto en español (y parcialmente en inglés fusionado).
    Devuelve el nombre normalizado del índice de referencia o None.
    """
    if not text:
        return None

    t = text.lower()
    t = re.sub(r"\s+", " ", t)

    # Cortocircuito: el fondo declara explícitamente que no tiene benchmark
    # Devolvemos "NO_BENCHMARK" (no None) para distinguir entre:
    #   NULL          = parser no encontró nada (incertidumbre)
    #   "NO_BENCHMARK"= KIID confirma explícitamente que no sigue ningún índice
    if _NO_BENCH_RE.search(t):
        return "NO_BENCHMARK"

    # ── Capa M: benchmarks de tipos monetarios (€STR, SOFR, EONIA, EURIBOR) ──
    # Estos fondos usan tipos de mercado como referencia, no índices de renta
    # fija/variable. El OCR frecuentemente pierde acentos (indice vs índice)
    # por lo que se usan patrones tolerantes con/sin acento.
    #
    # Formatos observados:
    #   "indice de referencia: €STR (in EUR)"       — Allianz (OCR sin acento)
    #   "utilizar el SOFR para comparar"            — BlackRock
    #   "utilizar el €STR para comparar"            — varios monetarios
    _MONEY_BENCH_RE = re.compile(
        r"(?:[íi]ndice\s+de\s+referencia\s*:\s*(€str|estr|ester|eonia|sofr|sonia|euribor[^\s,\.]{0,15}|tonar?)[^\n]{0,40})"
        r"|(?:utilizar\s+el\s+(€str|estr|ester|sofr|sonia|eonia|euribor[^\s,\.]{0,15})\s+para\s+comparar)"
        r"|(?:comparar\s+la\s+rentabilidad[^\.]{0,40}(€str|estr|ester|sofr|sonia|eonia|euribor[^\s,\.]{0,15}))"
        # DDF: "en consonancia con el tipo EURIBOR/ESTR a X meses" (Amundi, BNP, Schroders)
        r"|(?:en\s+consonancia\s+con\s+(?:el\s+tipo\s+)?(€str|estr|ester|eonia|sofr|sonia|euribor[^\s,\.]{0,15}))"
        # DDF: "rentabilidad acorde con los tipos de los mercados monetarios / RATE"
        r"|(?:rentabilidad\s+acorde\s+con[^\.]{0,60}(€str|estr|ester|eonia|sofr|sonia|euribor[^\s,\.]{0,15}))"
        # DDF: "índice de referencia:" con texto intermedio largo antes del tipo (hasta 200 chars)
        r"|(?:[íi]ndice\s+de\s+referencia\s*:[^\n\.]{0,200}(€str|estr|ester|eonia|sofr|sonia|euribor[^\s,\.]{0,15}))",
        re.IGNORECASE
    )
    m_money = _MONEY_BENCH_RE.search(t)
    if m_money:
        # Tomar el primer grupo capturado no vacío
        bench_name = next((g for g in m_money.groups() if g), None)
        if bench_name:
            return bench_name.strip().upper()

    # ── Capa L0: texto OCR fusionado con etiqueta "índicedereferencia..." ─────
    # En PDFs de dos columnas (JPMorgan, Amundi) el extractor fusiona las palabras
    # sin espacios: "índicedereferenciadelaclasedeacciones<BENCHMARK>apartado..."
    # El benchmark se halla entre la etiqueta y "usosysemejanza" o "metodode".
    # Se trabaja sobre t_fused (texto sin espacios) para encontrar la etiqueta,
    # y sobre t (espacios normalizados) para extraer el candidato con _trim_benchmark.
    t_fused_bench = t.replace(" ", "")
    # v4 FIX-BENCH-6/11: labels ampliados, end_markers incluyen subfondo/s
    _FUSED_LABELS = [
        "indicedereferenciadelaclasedeacciones",
        "índicedeferenciadela clasedeacciones",
        "indicedereferencia:",
        "indicedereferenciadelaclase",
        "índicedeferenciadela clase",
    ]
    for _label in _FUSED_LABELS:
        _pos = t_fused_bench.find(_label)
        if _pos < 0:
            continue
        # Extraer lo que sigue a la etiqueta hasta los marcadores de fin de sección
        _after_fused = t_fused_bench[_pos + len(_label):]
        _end_markers = ["usosysemejanza", "metodode", "otrasinversiones", "tecnicasinstrumentos",
                        "subfondos", "subfondo", "usodederivados", "estemétodo"]
        _end_pos = len(_after_fused)
        for _m in _end_markers:
            _p = _after_fused.find(_m)
            if _p >= 5:
                _end_pos = min(_end_pos, _p)
        _raw_fused = _after_fused[:min(_end_pos, 80)]
        _raw_fused = re.sub(r"[\x00-\x1f\x7f]", " ", _raw_fused)  # strip control chars from PDF
        if len(_raw_fused) < 5:
            continue
        # Insertar espacio delante de proveedores conocidos para que _trim_benchmark los encuentre
        _spacing_re = re.compile(r"(msci|bloomberg|ftse|russell|s&p|iboxx|topix|nikkei|stoxx|ice|bofa|nasdaq|korea|kospi|€str|estr|sofr|euribor|jpmorgan)")
        _spaced = _spacing_re.sub(r" ", _raw_fused).strip()
        # Eliminar el artefacto "apartado" (cabecera de columna derecha)
        _spaced = re.sub(r"apartado", " ", _spaced).strip()
        _spaced = re.sub(r"\s{2,}", " ", _spaced)
        result = _trim_benchmark(_spaced)
        if result:
            return result

    # ── Capa L1: triggers contextuales fuertes ────────────────────────────────
    # v4: se aplican sobre t_orig (texto original lowercased SIN normalizar \n→space)
    # para que el patrón "índice de referencia\n<benchmark>" funcione correctamente.
    t_orig = text.lower()
    for rx in _L1_PATTERNS:
        # Aplicar primero sobre texto con \n preservados, luego sobre t normalizado
        for src in [t_orig, t]:
            for m in re.finditer(rx, src):
                result = _trim_benchmark(m.group(1))
                if result:
                    return result

    # ── Capa L2: triggers de acción del gestor ────────────────────────────────
    for rx in _L2_PATTERNS:
        for m in re.finditer(rx, t):
            result = _trim_benchmark(m.group(1))
            if result:
                return result

    # ── Capa L3: reconocimiento directo del proveedor ─────────────────────────
    for rx in _L3_PATTERNS:
        for m in re.finditer(rx, t):
            result = _trim_benchmark(m.group(1))
            if result:
                return result

    # ── Texto OCR fusionado (sin espacios): buscar patrón proveedor pegado ────
    t_fused = t.replace(" ", "")
    # Si el texto fusionado contiene "apartado" (artefacto de columna PDF), saltarlo
    # ya que contamina los nombres de índices con texto de sección
    if "apartado" not in t_fused:
        for provider_raw in ["msci", "bloomberg", "ftse", "russell", "s&p", "nasdaq",
                              "stoxx", "iboxx", "topix", "nikkei"]:
            pos = t_fused.find(provider_raw)
            if pos != -1:
                snippet_fused = t_fused[pos:pos + 60]
                # Skip if contains OCR layout noise
                if any(noise in snippet_fused for noise in ["apartado", "consult", "derivad"]):
                    continue
                for suffix in ["index", "netreturn", "nettotalreturn", "(nr)", "totalreturn"]:
                    idx_s = snippet_fused.find(suffix)
                    if idx_s != -1:
                        raw_candidate = snippet_fused[:idx_s + len(suffix)]
                        if len(raw_candidate) >= 8:
                            raw_candidate = re.sub(
                                r"(" + _BENCH_PROVIDERS + r")", r"\1 ", raw_candidate
                            )
                            result = _trim_benchmark(raw_candidate)
                            if result:
                                return result

    return None


# =================================================
# REPLICATION METHOD  (v2 — añade gestión activa)
# =================================================
# Nuevo valor: "ACTIVE" para fondos de gestión activa explícita.
# Ya existía PHYSICAL y SYNTHETIC.
# Análisis empírico: 317 fondos dicen "gestiona de forma activa",
# 134 dicen "gestión activa" — ninguno capturado actualmente.
# -------------------------------------------------

ES_REPLICATION_PHYSICAL = [
    r"\bréplica\s+f[ií]sica\b",
    r"\breplicaci[oó]n\s+f[ií]sica\b",
    r"\binversi[oó]n\s+directa\s+en\s+los\s+valores\b",
]

ES_REPLICATION_SYNTHETIC = [
    r"\bréplica\s+sint[eé]tica\b",
    r"\breplicaci[oó]n\s+sint[eé]tica\b",
]

# FIX-REPL-3: nuevo valor PASSIVE — fondos indexados/ETF que replican pasivamente
ES_REPLICATION_PASSIVE = [
    r"\bgesti[oó]n\s+pasiva\b",
    r"\bgestiona(?:do)?\s+de\s+forma\s+pasiva\b",
    r"\binversi[oó]n\s+pasiva\b",
    # NOTA: "error de seguimiento" eliminado — aparece también en fondos activos
    # Candriam y similares lo usan para describir la banda de desviación respecto
    # al benchmark sin que el fondo sea de gestión pasiva.
    r"\bseguimiento\s+(?:del|al)\s+[ií]ndice\b",
    r"\breplicar\s+la\s+rentabilidad\s+del\s+[ií]ndice\b",
    r"\breplicar\s+(?:el\s+comportamiento|los?\s+resultados)\s+del\s+[ií]ndice\b",
    r"\breplicaci[oó]n\s+del\s+[ií]ndice\b",
]

ES_REPLICATION_ACTIVE = [
    r"\bgestiona(?:do)?\s+de\s+forma\s+activa\b",
    r"\bgesti[oó]n\s+activa\b",
    r"\bfondo\s+(?:es\s+)?de\s+gesti[oó]n\s+activa\b",
    r"\bgestionado\s+activamente\b",
    # FIX-REPL-4: "el fondo se gestiona activamente" (Deutsche/DWS, 3ª persona presente)
    r"\bgestiona\s+activamente\b",
    r"\bgestionada\s+activamente\b",
    r"\bgestiona\s+de\s+manera\s+activa\b",
]

EN_REPLICATION_PHYSICAL = [
    r"\bphysical\s+(?:full\s+)?replication\b",
    r"\bphysical\s+securities\b",
    r"\bfull\s+replication\b",
    r"\boptimis[ez]d?\s+(?:physical\s+)?replication\b",
]

EN_REPLICATION_SYNTHETIC = [
    r"\bsynthetic\s+replication\b",
]

# FIX-REPL-3 (EN): detección de réplica pasiva en KIIDs en inglés
EN_REPLICATION_PASSIVE = [
    r"\bpassively\s+managed\b",
    r"\bpassive\s+(?:fund\s+)?management\b",
    r"\bindex\s+tracking\b",
    r"\btracking\s+error\b",
    r"\btrack(?:s|ing)?\s+the\s+(?:performance\s+of\s+the\s+)?index\b",
    r"\breplicate(?:s)?\s+the\s+(?:performance|returns)\s+of\b",
]

EN_REPLICATION_ACTIVE = [
    r"\bactively\s+managed\b",
    r"\bactive\s+(?:fund\s+)?management\b",
]


def _detect_replication_method(text: str, language: Optional[str]) -> Optional[str]:
    if not text:
        return None

    t = text.lower()

    # ── Texto OCR fusionado: "gestionadeformaactiva" / "formaactiva" ─────────
    # 110/110 fondos con Language=None y replicación nula usan texto fusionado
    # donde "gestiona de forma activa" aparece como "gestionadeformaactiva".
    if language is None:
        t_fused = t.replace(" ", "")
        if any(p in t_fused for p in ["replicafísica", "replicafisica", "replicacionfisica"]):
            return "PHYSICAL"
        if "replicasintetica" in t_fused or "replicacionsint" in t_fused:
            return "SYNTHETIC"
        # FIX-REPL-3 (fused): detectar gestión pasiva antes de activa
        if any(p in t_fused for p in ["gestionadeformapasiva", "gestionpasiva",
                                       "errordeseseguimiento", "errordeseguimiento",
                                       "gestionpasiva", "pasiva"]):
            # Verificar con un poco más de contexto que sea realmente gestión pasiva
            if any(p in t_fused for p in ["gestionadeformapasiva", "gestionpasiva",
                                           "errordeseseguimiento", "errordeseguimiento"]):
                return "PASSIVE"
        if any(p in t_fused for p in ["gestionadeformaactiva", "gestionadoactivamente",
                                       "formaactiva", "gestiónactiva", "gestionactiva",
                                       "gestionaactivamente"]):
            return "ACTIVE"
        return None

    if language == "ES":
        for rx in ES_REPLICATION_PHYSICAL:
            if re.search(rx, t):
                return "PHYSICAL"
        for rx in ES_REPLICATION_SYNTHETIC:
            if re.search(rx, t):
                return "SYNTHETIC"
        # FIX-REPL-3: PASSIVE antes de ACTIVE (es más específico)
        # También verifica texto fusionado con Language=ES (BNP/Amundi ratio espacio bajo)
        t_ns = t.replace(" ", "")
        if "gestionadeformapasiva" in t_ns or "gestionpasiva" in t_ns:
            return "PASSIVE"
        for rx in ES_REPLICATION_PASSIVE:
            if re.search(rx, t):
                return "PASSIVE"
        for rx in ES_REPLICATION_ACTIVE:
            if re.search(rx, t):
                return "ACTIVE"

    if language == "EN":
        for rx in EN_REPLICATION_PHYSICAL:
            if re.search(rx, t):
                return "PHYSICAL"
        for rx in EN_REPLICATION_SYNTHETIC:
            if re.search(rx, t):
                return "SYNTHETIC"
        # FIX-REPL-3: PASSIVE antes de ACTIVE
        for rx in EN_REPLICATION_PASSIVE:
            if re.search(rx, t):
                return "PASSIVE"
        for rx in EN_REPLICATION_ACTIVE:
            if re.search(rx, t):
                return "ACTIVE"

    return None


# =================================================
# HEDGING POLICY  (v2 — cobertura ampliada)
# =================================================
# Gaps identificados:
#   - 30 fondos: "cubierto en EUR" / "cubierto frente a" → HEDGED (no detectado)
#   - 7 fondos: "sin cobertura" → UNHEDGED (no detectado)
#   - 120 fondos con "hedged" en texto inglés que Language=None (texto fusionado)
#     → el nombre del producto contiene "(hedged)" en inglés
#
# Nuevo: detección de hedging por nombre de clase en el propio texto
# (en fondos con OCR fusionado donde Language=None pero contienen "(hedged)")
# -------------------------------------------------

ES_HEDGED = [
    r"\bclase\s+(?:de\s+acciones\s+)?(?:est[aá]\s+)?cubierta\b",
    r"\bcubierta\s+frente\s+al\s+riesgo\s+de\s+divisa\b",
    r"\bcobertura\s+de\s+divisa\b",
    r"\bcubierta\s+frente\s+al\s+riesgo\s+de\s+tipo\s+de\s+cambio\b",
    # NUEVO: "cubierto frente a / cubierto en" — 30 casos
    r"\bcubierto\s+(?:en|frente\s+a)\b",
    # NUEVO: "cobertura cambiaria / de tipo de cambio"
    r"\bcobertura\s+(?:cambiaria|de\s+tipo\s+de\s+cambio)\b",
    # NUEVO: "clase con cobertura"
    r"\bclase\s+con\s+cobertura\b",
    # NUEVO: "(eur hedged)" o "(usd hedged)" en nombre de clase en ES
    r"\b(?:eur|usd|gbp|chf|jpy)\s+\(?\s*hedged\s*\)?",
    r"\(hedged\)",
]

ES_UNHEDGED = [
    r"\bno\s+est[aá]\s+cubierta\b",
    r"\bno\s+se\s+aplica\s+cobertura\s+de\s+divisa\b",
    r"\bsin\s+cobertura\s+de\s+divisa\b",
    # NUEVO: "sin cobertura" genérico — 7 casos
    r"\bsin\s+cobertura\b",
    # NUEVO: "no existe cobertura"
    r"\bno\s+existe\s+cobertura\b",
    # NUEVO: "riesgo de divisa no cubierto"
    r"\briesgo\s+de\s+(?:divisa|cambio)\s+no\s+(?:est[aá]\s+)?cubierto\b",
    # NUEVO: "no se cubre el riesgo de divisa"
    r"\bno\s+se\s+cubre\s+(?:el\s+)?(?:riesgo\s+de\s+)?divisa\b",
]

ES_PARTIAL = [
    r"\bcobertura\s+parcial\b",
    r"\bparcialmente\s+cubierta\b",
]

EN_HEDGED = [
    r"\bcurrency\s+hedged\b",
    r"\bshare\s+class\s+is\s+(?:fully\s+)?hedged\b",
    # NUEVO: "EUR (Hedged)" en nombre del producto
    r"\b(?:eur|usd|gbp|chf|jpy)\s*\(\s*hedged\s*\)",
    r"\(hedged\)",
    # NUEVO: clase con nombre "hedged" sin calificador negativo
    r"\bhedged\s+(?:class|share|accumulation|income)\b",
    r"\bhedged\s+(?:eur|usd|gbp)\b",
]

EN_UNHEDGED = [
    r"\bnot\s+hedged\b",
    r"\bshare\s+class\s+is\s+not\s+hedged\b",
    r"\bunhedged\b",
    r"\bnon-hedged\b",
]

EN_PARTIAL = [
    r"\bpartially\s+hedged\b",
]


def _detect_hedging_policy(text: str, language: Optional[str]) -> Optional[str]:
    """
    Detecta política de cobertura de divisa.
    v2: cubre texto fusionado y nuevas formulaciones.
    """
    if not text:
        return None

    t = text.lower()

    # ── Detección independiente de idioma para texto fusionado ───────────────
    # El texto OCR fusionado (sin espacios) puede contener "(hedged)" o "unhedged"
    # en el nombre del producto sin que Language sea detectada
    if language is None:
        # Buscar en el texto sin espacios
        t_fused = t.replace(" ", "")
        if "unhedged" in t_fused or "nothedged" in t_fused:
            return "UNHEDGED"
        # "hedgedtoeur/usd/..." — aparece en nombres de clase y en benchmarks
        if ("(hedged)" in t_fused
                or re.search(r"(?:eur|usd|gbp|chf)\(hedged\)", t_fused)
                or re.search(r"hedgedto(?:eur|usd|gbp|chf|jpy)", t_fused)):
            return "HEDGED"
        return None

    if language == "ES":
        for rx in ES_PARTIAL:
            if re.search(rx, t):
                return "PARTIAL"
        for rx in ES_UNHEDGED:
            if re.search(rx, t):
                return "UNHEDGED"
        for rx in ES_HEDGED:
            if re.search(rx, t):
                return "HEDGED"

    if language == "EN":
        for rx in EN_PARTIAL:
            if re.search(rx, t):
                return "PARTIAL"
        for rx in EN_UNHEDGED:
            if re.search(rx, t):
                return "UNHEDGED"
        for rx in EN_HEDGED:
            if re.search(rx, t):
                return "HEDGED"

    return None


# =================================================
# FUND CURRENCY  (v2 — cobertura ampliada)
# =================================================
# Gaps identificados:
#   - 112 fondos: "divisa: EUR" / "moneda: EUR" → patrón label:valor
#   - 27 fondos: "moneda de la clase de acciones es EUR"
#   - 19 fondos: "la moneda del fondo es EUR"
#
# Nuevo: añadir estos tres patrones en alta prioridad.
# Mantener los existentes (divisa de referencia, moneda base).
# -------------------------------------------------

ES_CURRENCY_PATTERNS = [
    # NUEVO alta prioridad: "divisa: EUR" / "moneda: EUR" (112 fondos)
    r"\b(?:divisa|moneda)\s*[:\-]\s*([A-Z]{3})\b",

    # NUEVO: "la moneda del fondo es EUR" (19 fondos, DE0009...)
    r"\b(?:la\s+)?(?:divisa|moneda)\s+del\s+fondo\s+es\s+([A-Z]{3})\b",

    # NUEVO: "moneda/divisa de la clase de acciones es EUR" (27 fondos)
    r"\b(?:la\s+)?(?:divisa|moneda)\s+de\s+la\s+clase\s+de\s+acciones\s+es\s+([A-Z]{3})\b",

    # Existente: "divisa de referencia [de la clase de participaciones] es EUR"
    r"\bdivisa\s+de\s+referencia\s+(?:de\s+la\s+clase\s+de\s+participaciones\s+)?es\s+([A-Z]{3})\b",

    # Existente: "moneda base [del fondo] es EUR"
    r"\bmoneda\s+base\s+(?:del\s+fondo\s+)?es\s+([A-Z]{3})\b",

    # v4 FIX-CURR-2: "moneda base del Fondo/Subfondo es [el/la] dólar/euro/..."
    r"\bmoneda\s+base\s+del\s+(?:fondo|subfondo)\s+es\s+(?:el\s+|la\s+)?([a-záéíóúü\w\s]+?)(?:\.|,|\n|$)",

    # Existente: "denominado en euros / dólares"
    r"\bdenominad[oa]\s+en\s+(euros|d[oó]lares|libras|yenes)\b",

    # NUEVO: "denominación: EUR"
    r"\bdenominaci[oó]n\s*[:\-]\s*([A-Z]{3})\b",

    # v4 FIX-CURR-3: "Divisa de referencia Dólar estadounidense (USD)" — tabular BlackRock/Fidelity
    r"\bdivisa\s+de\s+referencia\s+[^\n\(\)\.]{0,40}?\(([A-Z]{3})\)",
]

EN_CURRENCY_PATTERNS = [
    # Existente
    r"\b(?:base|reference)\s+currency\s+(?:of\s+the\s+fund\s+)?is\s+([A-Z]{3})\b",
    # NUEVO: "currency: EUR" / "currency - EUR"
    r"\bcurrency\s*[:\-]\s*([A-Z]{3})\b",
    # NUEVO: "share class currency is EUR"
    r"\bshare\s+class\s+currency\s+is\s+([A-Z]{3})\b",
    # NUEVO: "denominated in USD"
    r"\bdenominated\s+in\s+(USD|EUR|GBP|CHF|JPY|SEK|NOK|DKK|AUD|CAD)\b",
]


def _normalize_currency(val: str) -> Optional[str]:
    if not val:
        return None

    v = val.strip()

    # v4 FIX-CURR-4: formas compuestas (dólar estadounidense, libra esterlina, etc.)
    MAP = {
        "euros": "EUR",
        "euro": "EUR",
        "dólares": "USD",
        "dolares": "USD",
        "dólar": "USD",
        "dolar": "USD",
        "dólares estadounidenses": "USD",
        "dólar estadounidense": "USD",
        "dolares estadounidenses": "USD",
        "dolar estadounidense": "USD",
        "libras": "GBP",
        "libra": "GBP",
        "libras esterlinas": "GBP",
        "libra esterlina": "GBP",
        "yenes": "JPY",
        "yen": "JPY",
        "yenes japoneses": "JPY",
        "yen japonés": "JPY",
        "francos suizos": "CHF",
        "franco suizo": "CHF",
        "coronas suecas": "SEK",
        "corona sueca": "SEK",
        "coronas noruegas": "NOK",
        "corona noruega": "NOK",
        "coronas danesas": "DKK",
        "corona danesa": "DKK",
        "dólares canadienses": "CAD",
        "dólar canadiense": "CAD",
        "dólares australianos": "AUD",
        "dólar australiano": "AUD",
    }

    v_low = v.lower()
    if v_low in MAP:
        return MAP[v_low]

    v_up = v.upper()
    if re.fullmatch(r"[A-Z]{3}", v_up):
        return v_up

    return None


def _detect_fund_currency(text: str, language: Optional[str]) -> Optional[str]:
    if not text:
        return None

    # ── Alta prioridad: divisa desde tabla PRIIPs "Costes totales X EUR" ─────
    # El 91% de los KIIDs son PRIIPs con esta sección. La divisa aparece
    # de forma muy fiable junto al importe de costes. Formatos observados:
    #   "Costes totales 595 EUR"
    #   "Costes totales EUR 30"
    #   "Costes totales 54 €"
    #   "Costes totales 8 USD"
    _COSTS_CURR_RE = re.compile(
        r'costes\s+totales\s+'
        r'(?:'
        r'(EUR|USD|GBP|JPY|CHF|SEK|NOK|DKK|AUD|CAD|PLN|CZK|HUF|€|\$|£|¥)'  # divisa antes
        r'|[\d\s,\.]+\s*(EUR|USD|GBP|JPY|CHF|SEK|NOK|DKK|AUD|CAD|PLN|CZK|HUF|€|\$|£|¥)'  # divisa después
        r')',
        re.IGNORECASE
    )
    m_cost = _COSTS_CURR_RE.search(text)
    if m_cost:
        raw = (m_cost.group(1) or m_cost.group(2) or "").strip()
        # Normalizar símbolos
        _SYM = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY"}
        raw = _SYM.get(raw, raw.upper())
        result = _normalize_currency(raw)
        if result:
            return result

    # ── Divisa base implícita desde contexto de restricción de divisa ─────────
    # Patrón: "La exposición a divisas distintas del EUR no superará el X%"
    # Muy fiable: indica inequívocamente que la divisa base es EUR (o USD, etc.)
    # Observado en: Allianz, DWS y otros KIIDs donde Costes totales no lleva importe
    _IMPLICIT_CURR_RE = re.compile(
        r'divisas?\s+distintas?\s+(?:del?|de\s+la)\s+(EUR|USD|GBP|JPY|CHF|SEK|NOK)',
        re.IGNORECASE
    )
    m_impl = _IMPLICIT_CURR_RE.search(text)
    if m_impl:
        result = _normalize_currency(m_impl.group(1).upper())
        if result:
            return result

    # ── Divisa desde nombre de tipo de interés "(in EUR/USD)" ────────────────
    # Patrón: "índice de referencia: €STR (in EUR)" — la divisa entre paréntesis
    # indica la denominación del índice y por extensión la del fondo
    _IN_CURR_RE = re.compile(
        r'\bin\s+(EUR|USD|GBP|JPY|CHF)\b',
        re.IGNORECASE
    )
    m_in = _IN_CURR_RE.search(text)
    if m_in:
        result = _normalize_currency(m_in.group(1).upper())
        if result:
            return result

    # ── Texto OCR fusionado: "monedabasedelsubfondo:EUR" ─────────────────────
    # 103/130 fondos con Language=None usan este patrón. Alta prioridad porque
    # es exacto: "monedabasedelsubfondo:<ISO3>" sin ambigüedad.
    t_fused = text.lower().replace(" ", "")
    m_fused = re.search(r"monedabasedelsubfondo:([a-z]{3})", t_fused)
    if m_fused:
        result = _normalize_currency(m_fused.group(1).upper())
        if result:
            return result
    # Variante: "monedabasedelfondo:" (DWS, otros)
    m_fused2 = re.search(r"monedabase(?:del(?:subfondo|fondo)|delsubfondo):([a-z]{3})", t_fused)
    if m_fused2:
        result = _normalize_currency(m_fused2.group(1).upper())
        if result:
            return result

    # Para texto fusionado o sin idioma, intentar con el texto original
    effective_lang = language if language else "ES"  # KIIDs son mayoritariamente ES

    if effective_lang in ("ES", None):
        for rx in ES_CURRENCY_PATTERNS:
            m = re.search(rx, text)
            if not m:
                m = re.search(rx, text.lower())
            if m:
                val = m.group(m.lastindex)
                if val:
                    val = val.strip().rstrip(".,")
                result = _normalize_currency(val)
                if result:
                    return result

    if effective_lang == "EN":
        for rx in EN_CURRENCY_PATTERNS:
            m = re.search(rx, text)
            if not m:
                m = re.search(rx, text.lower())
            if m:
                val = m.group(m.lastindex)
                result = _normalize_currency(val)
                if result:
                    return result

    return None


# =================================================
# PORTFOLIO CURRENCY  (sin cambios, reproducido)
# =================================================

ES_PORTFOLIO_CURRENCY_PATTERNS = [
    r"\bmoneda\s+de\s+referencia\s+de\s+la\s+cartera\s+es\s+([A-Z]{3})\b",
    r"\bmoneda\s+de\s+referencia\s+del\s+fondo\s+es\s+([A-Z]{3})\b",
    r"\bla\s+cartera\s+se\s+gestiona\s+en\s+([A-Z]{3})\b",
]

EN_PORTFOLIO_CURRENCY_PATTERNS = [
    r"\breference\s+currency\s+of\s+the\s+portfolio\s+is\s+([A-Z]{3})\b",
    r"\bportfolio\s+currency\s+is\s+([A-Z]{3})\b",
]


def _detect_portfolio_currency(text: str, language: Optional[str]) -> Optional[str]:
    if not text:
        return None

    # ── Texto OCR fusionado ───────────────────────────────────────────────────
    t_fused = text.lower().replace(" ", "")
    m_f = re.search(r"carteraprincipalmente(?:en|enmoneda)([a-z]{3})", t_fused)
    if m_f:
        r = _normalize_currency(m_f.group(1).upper())
        if r:
            return r

    if not language:
        return None

    if language == "ES":
        for rx in ES_PORTFOLIO_CURRENCY_PATTERNS:
            m = re.search(rx, text)
            if m:
                return m.group(1)

    if language == "EN":
        for rx in EN_PORTFOLIO_CURRENCY_PATTERNS:
            m = re.search(rx, text)
            if m:
                return m.group(1)

    return None


# ============================================================
# PASO 10 — Ongoing Charge (gastos corrientes / incidencia de costes)
# ============================================================
#
# HALLAZGO (análisis de 3.204 KIIDs reales):
#   - 91.4% son formato PRIIPs con tabla de "Incidencia anual de los costes"
#   - Solo 2.5% son UCITS antiguos con "Gastos corrientes X,XX%"
#
# La tabla PRIIPs tiene DOS columnas:
#   Col1: coste si se sale en 1 año (incluye entrada amortizada → más alta)
#   Col2: coste en el periodo recomendado completo → proxy del TER real
#
# Ejemplo real:
#   "Incidencia anual de los costes (*) 5,9%  2,7%  cada año"
#                                        ↑Col1  ↑Col2=Ongoing
#
# Se toma SIEMPRE el segundo valor (Col2) cuando existen dos.
# Si solo hay un valor (KIIDs con periodo=1 año), se usa ese.
#
# Rango válido ampliado: 0.01%-10%.
# Los PRIIPs incluyen costes de transacción, distribución y gestión,
# por lo que valores >3% son posibles en alternativas/emergentes.
#
# Variantes de nombre del campo observadas:
#   ES: "Incidencia anual de los costes"  (dominante)
#       "Incidencia de los costes"
#       "Impacto anual en los costes"
#       "Gastos corrientes"  (UCITS antiguo)
#       "Gastos en curso"    (PRIIPs variante)
#   Fusionado: "incidenciadeloscostesx.x%"

_OC_MIN = 0.0001   # 0.01%
_OC_MAX = 0.1000   # 10.0%  (PRIIPs incluye todos los costes)

# ── DDF/PRIIPs "Composición de costes" ────────────────────────────────────────
# Extrae los costes CORRIENTES (gestión + operación) ignorando entrada/salida.
# Ejemplo DDF:
#   "Comisiones de gestión y otros costes administrativos  El 0,66 %"
#   "Costes de operación  El 0,03 %"
# La suma (0,69%) es el TER real, no la "Incidencia anual" que incluye entrada.

# Comisiones de gestión (primera línea de costes corrientes)
_OC_DDF_MGMT_RE = re.compile(
    r"comisiones?\s+de\s+gesti[oó]n\s+y\s+otros\s+costes[^\.]{0,150}"
    r"El\s+([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Costes de operación (transacción)
_OC_DDF_TRANS_RE = re.compile(
    r"costes?\s+de\s+operaci[oó]n[^\.]{0,150}"
    r"El\s+([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Comisión de entrada = 0 explícito — DDF declara "sin comisión de entrada" (v17)
# Equivalente a _EXIT_FEE_ZERO_RE ya existente para la comisión de salida.
_ENTRY_FEE_ZERO_RE = re.compile(
    r"costes?\s+de\s+entrada[^\r\n]{0,200}"
    r"(?:no\s+se\s+cobr(?:an|a)\s+(?:gastos|comisi[oó]n)\s+de\s+entrada"
    r"|no\s+entry\s+(?:charge|fee)"
    r"|entry\s+(?:charge|fee)\s*:\s*(?:none|nil|0)"
    r"|comisi[oó]n\s+de\s+entrada\s*:\s*0"
    r"|gastos\s+de\s+entrada\s*:\s*0"
    r"|sin\s+comisi[oó]n\s+de\s+entrada"
    r"|\b0(?:[,.]00)?\s*(?:eur|usd|gbp|%)\b)",
    re.IGNORECASE | re.DOTALL
)

# Comisión de entrada (Entry_Fee_Pct)
_ENTRY_FEE_RE = re.compile(
    r"costes?\s+de\s+entrada[^\r\n]{0,300}"
    r"(?:hasta\s+)?([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Comisión de salida (Exit_Fee_Pct) — valor no cero
_EXIT_FEE_RE = re.compile(
    r"costes?\s+de\s+salida[^\r\n]{0,300}"
    r"([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Comisión de salida = 0 explícito — DDF declara "0 EUR" o "no cobramos"
_EXIT_FEE_ZERO_RE = re.compile(
    r"costes?\s+de\s+salida[^\r\n]{0,200}"
    r"(?:no\s+cobr(?:amos|a)\s+comisi[oó]n\s+de\s+salida"
    r"|\b0(?:[,.]00)?\s*(?:eur|usd|gbp|%)"
    r"|sin\s+comisi[oó]n\s+de\s+salida"
    r"|no\s+se\s+aplica\s+comisi[oó]n\s+de\s+salida)",
    re.IGNORECASE | re.DOTALL
)

# Patrón principal PRIIPs: captura uno o dos valores porcentuales
_OC_PRIIPS_RE = re.compile(
    r'(?:incidencia\s+(?:anual\s+)?de\s+los\s+costes'
    r'|impacto\s+(?:anual\s+)?en\s+los\s+costes'
    r'|gastos\s+en\s+curso)'
    r'[^0-9]{0,50}'           # separador (asterisco, espacios, etc.)
    r'([\d]+[,.][\d]+)\s*%'   # primer valor (siempre presente)
    r'(?:\s+([\d]+[,.][\d]+)\s*%)?',  # segundo valor (opcional)
    re.IGNORECASE
)

# Patrón UCITS antiguo: "Gastos corrientes X,XX%"
_OC_UCITS_RE = re.compile(
    r'gastos\s+corrientes\s*[:\|]?\s*([\d]+[,.][\d]+)\s*%',
    re.IGNORECASE
)

# Patrón fusionado (OCR sin espacios)
_OC_FUSED_PATTERNS = [
    re.compile(r'incidenciadeloscoste[s]?[^0-9]{0,15}([\d]+[,.][\d]+)%([\d]+[,.][\d]+)?%?'),
    re.compile(r'gastoscorrientes([\d]+[,.][\d]+)%'),
]


def _parse_oc_pct(raw: str) -> Optional[float]:
    """Convierte string porcentaje a float decimal. Retorna None si fuera de rango."""
    try:
        val = float(raw.replace(",", ".")) / 100
        if _OC_MIN <= val <= _OC_MAX:
            return round(val, 6)
        return None
    except (ValueError, TypeError):
        return None


def _detect_entry_fee(text: str) -> Optional[float]:
    """
    Extrae la comisión de entrada (Entry_Fee_Pct) desde la sección
    "Composición de costes" del DDF/PRIIPs.

    Prioridad (v17):
    1. Declaración explícita de sin comisión → retorna 0.0 (ZERO_CONFIRMED)
    2. Valor porcentual no cero             → retorna float decimal (EXTRACTED)
    3. Sin detección                        → retorna None (NOT_FOUND)

    La distinción 0.0 vs None es crítica para Fee_Known_Flag en P3:
    un fondo con Entry_Fee_Pct=NULL puede cobrar hasta un 6% de entrada.
    """
    if not text:
        return None

    # Prioridad 1: declaración explícita de sin comisión de entrada
    m_zero = _ENTRY_FEE_ZERO_RE.search(text) or _ENTRY_FEE_ZERO_RE.search(text.lower())
    if m_zero:
        return 0.0

    # Prioridad 2: valor porcentual no cero
    m = _ENTRY_FEE_RE.search(text) or _ENTRY_FEE_RE.search(text.lower())
    if m:
        val = _parse_oc_pct(m.group(1))
        if val is not None and val <= 0.10:
            return val
    return None


def _detect_exit_fee(text: str) -> Optional[float]:
    """
    Extrae la comisión de salida (Exit_Fee_Pct) desde la sección
    "Composición de costes" del DDF/PRIIPs.

    Prioridad:
    1. Detectar declaración explícita de 0 ("no cobramos", "0 EUR") → 0.0
    2. Detectar porcentaje no cero → float decimal
    3. Sin detección → None (desconocido)

    La distinción 0.0 vs None es crítica para P3:
    0.0 = confirmado sin comisión | None = no determinado (KIID clásico)
    """
    if not text:
        return None

    # Prioridad 1: declaración explícita de cero
    m_zero = _EXIT_FEE_ZERO_RE.search(text) or _EXIT_FEE_ZERO_RE.search(text.lower())
    if m_zero:
        return 0.0

    # Prioridad 2: valor porcentual no cero
    m = _EXIT_FEE_RE.search(text) or _EXIT_FEE_RE.search(text.lower())
    if m:
        val = _parse_oc_pct(m.group(1))
        if val is not None and 0 < val <= 0.05:
            return val

    return None


def _detect_ongoing_charge(text: str, language: Optional[str]) -> Optional[float]:
    """
    Extrae los gastos corrientes (ongoing charges / incidencia de costes).

    Lógica de prioridad:
    0. DDF "Composición de costes": suma gestión + operación (TER real)
       — evita capturar la "Incidencia anual" que incluye entrada amortizada
    1. Patrón PRIIPs con dos valores → tomar el segundo (periodo recomendado)
    2. Patrón PRIIPs con un valor → usar ese
    3. Patrón UCITS antiguo ("Gastos corrientes X%")
    4. Patrón fusionado OCR (sin espacios)

    Devuelve float decimal (ej. 0.0075 para 0.75%) o None.
    """
    if not text:
        return None

    # ── 0: DDF Composición de costes (Prioridad máxima) ──────────────────────
    # Suma comisiones de gestión + costes de operación = TER real
    # Evita el error de capturar "Incidencia anual" que incluye entrada
    m_mgmt  = _OC_DDF_MGMT_RE.search(text) or _OC_DDF_MGMT_RE.search(text.lower())
    m_trans = _OC_DDF_TRANS_RE.search(text) or _OC_DDF_TRANS_RE.search(text.lower())
    if m_mgmt:
        mgmt_val  = _parse_oc_pct(m_mgmt.group(1))
        trans_val = _parse_oc_pct(m_trans.group(1)) if m_trans else 0.0
        if mgmt_val is not None:
            ter = round(mgmt_val + (trans_val or 0.0), 6)
            if _OC_MIN <= ter <= _OC_MAX:
                return ter

    # ── 1 y 2: Patrón PRIIPs (dominante: 91% de fondos) ─────────────────────
    m = _OC_PRIIPS_RE.search(text)
    if not m:
        m = _OC_PRIIPS_RE.search(text.lower())
    if m:
        v2_str = m.group(2)   # segundo valor (periodo recomendado) — puede ser None
        v1_str = m.group(1)   # primer valor (siempre existe)
        if v2_str:
            val = _parse_oc_pct(v2_str)
            if val is not None:
                return val
        # Un solo valor: KIIDs con periodo=1 año
        val = _parse_oc_pct(v1_str)
        if val is not None:
            return val

    # ── 3: UCITS antiguo "Gastos corrientes X,XX%" ───────────────────────────
    m_ucits = _OC_UCITS_RE.search(text)
    if not m_ucits:
        m_ucits = _OC_UCITS_RE.search(text.lower())
    if m_ucits:
        val = _parse_oc_pct(m_ucits.group(1))
        if val is not None:
            return val

    # ── 4: Texto OCR fusionado ────────────────────────────────────────────────
    t_fused = text.lower().replace(" ", "")
    for rx in _OC_FUSED_PATTERNS:
        m_f = rx.search(t_fused)
        if m_f:
            # Tomar grupo 2 si existe (segundo valor), si no grupo 1
            raw = (m_f.group(2) or m_f.group(1)) if m_f.lastindex >= 2 else m_f.group(1)
            val = _parse_oc_pct(raw)
            if val is not None:
                return val

    return None


# =================================================
# ACCUMULATION_POLICY — acumulación vs distribución
# =================================================
# DDF: "clase de acciones que no es de distribución, los ingresos se reinvierten"
#      "clase de distribución, los ingresos se distribuyen"
# KIID clásico: "acumulación" / "distribución" / "reparto"

_ACCUM_PATTERNS_ES = [
    r"clase\s+de\s+acciones?\s+(?:que\s+)?no\s+(?:es\s+)?de\s+distribuci[oó]n",
    r"ingresos\s+de\s+las\s+inversiones\s+se\s+reinvierten",
    r"clase\s+de\s+acumulaci[oó]n",
    r"participaciones?\s+de\s+acumulaci[oó]n",
    r"acumulaci[oó]n.{0,30}no\s+distribuye",
    r"no\s+reparte\s+dividendos",
    r"no\s+distribuye\s+(?:dividendos|rentas|ingresos)",
]

_DIST_PATTERNS_ES = [
    r"clase\s+de\s+distribuci[oó]n",
    r"clase\s+de\s+acciones?\s+de\s+distribuci[oó]n",
    r"distribuye\s+(?:dividendos|rentas|ingresos)",
    r"reparte\s+(?:dividendos|rentas)",
    r"participaciones?\s+de\s+distribuci[oó]n",
    r"pol[íi]tica\s+de\s+distribuci[oó]n[^\.]{0,80}distribuye",
]

_ACCUM_PATTERNS_EN = [
    r"accumulation\s+(?:share|unit|class)",
    r"income\s+is\s+(?:reinvested|accumulated)",
    r"does\s+not\s+pay\s+(?:a\s+)?dividend",
    r"non.distributing",
]

_DIST_PATTERNS_EN = [
    r"distribution\s+(?:share|unit|class)",
    r"income\s+(?:is\s+)?(?:distributed|paid\s+out)",
    r"pays?\s+(?:a\s+)?dividend",
    r"distributing\s+(?:share|class)",
]


def _detect_accumulation_policy(text: str, language: Optional[str]) -> Optional[str]:
    """
    Detecta si el fondo es de acumulación o distribución.
    Devuelve 'ACCUMULATION', 'DISTRIBUTION' o None.
    """
    if not text:
        return None
    t = text.lower()

    if language in ("ES", None):
        for rx in _ACCUM_PATTERNS_ES:
            if re.search(rx, t, re.IGNORECASE):
                return "ACCUMULATION"
        for rx in _DIST_PATTERNS_ES:
            if re.search(rx, t, re.IGNORECASE):
                return "DISTRIBUTION"

    if language in ("EN", None):
        for rx in _ACCUM_PATTERNS_EN:
            if re.search(rx, t, re.IGNORECASE):
                return "ACCUMULATION"
        for rx in _DIST_PATTERNS_EN:
            if re.search(rx, t, re.IGNORECASE):
                return "DISTRIBUTION"

    return None


# =================================================
# SFDR_ARTICLE — Artículo SFDR (6, 8, 9)
# =================================================

def _detect_sfdr_article(text: str) -> Optional[int]:
    """
    Detecta el artículo SFDR del fondo desde el texto KIID/DDF.
    Art. 9 > Art. 8 > Art. 6 (por especificidad).
    Devuelve 9, 8 o 6. NULL si no se puede determinar.
    """
    if not text:
        return None
    t = text.lower()

    # ── Art. 9 — objetivo de inversión sostenible ───────────────────────
    if any(k in t for k in [
        "artículo 9", "article 9", "articulo 9",
        "objetivo de inversión sostenible",
        "sustainable investment objective",
        "art. 9",
        # OCR fusionado JPMorgan
        "artículo9delreglamento",
    ]):
        return 9

    # ── Art. 8 — promueve características medioambientales/sociales ─────
    if any(k in t for k in [
        "artículo 8", "article 8", "articulo 8",
        "características medioambientales y sociales",
        "environmental and social characteristics",
        "promueve características medioambientales",
        "promotes environmental or social characteristics",
        "art. 8",
        # OCR fusionado JPMorgan (sin espacio)
        "artículo8delreglamento",
        # Variante con salto de línea en OCR
                # Señal descriptiva genérica (DDF modernos)
        "características medioambientales",   # sin "y sociales" (suficiente)
        "promote environmental",              # EN sin "or social"
        # Reglamento SFDR explícito + artículo 8 simultáneamente
        # Nota: "reglamento 2019/2088" sola NO es señal de Art.8 (aparece en Art.6 también)
        "sfdr article 8",
    ]):
        # Guardia: no asignar Art.8 si el texto indica explícitamente Art.6
        if not any(neg in t for neg in [
            "no promueve características",
            "does not promote environmental",
            "no tiene en cuenta criterios",
        ]):
            return 8

    # ── Art. 6 — declara explícitamente que no es Art.8/9 ──────────────
    if any(k in t for k in [
        "no promueve características medioambientales",
        "does not promote environmental",
        "no tiene en cuenta los criterios",
        "no considera factores de sostenibilidad",
    ]):
        return 6

    return None  # No determinable — no forzar Art.6


# =================================================
# RECOMMENDED_HOLDING_PERIOD
# =================================================

_RHP_RE = re.compile(
    r"per[íi]odo\s+de\s+mantenimiento\s+recomendado\s*[:\-]?\s*([^\n\.]{3,40})",
    re.IGNORECASE
)
_RHP_EN_RE = re.compile(
    r"recommended\s+holding\s+period\s*[:\-]?\s*([^\n\.]{3,40})",
    re.IGNORECASE
)

# Filtros de rechazo — texto narrativo capturado por error
_RHP_REJECT_RE = re.compile(
    r"^(?:se\s+basa|,\s*y\s+que|of\s+at|:\s*0|y\s+que)"
    r"|^\d{1,2}-\d{1,2}-\d{4}",  # fechas DD-MM-YYYY (vencimiento, no horizonte)
    re.I
)

# Validación mínima — el raw debe contener al menos un dígito o palabra de período
_RHP_VALID_RE = re.compile(r"\d|a[ñn]o|year|mes|month|d[íi]a|day", re.I)

_RHP_NORMALIZER = [
    # Período específico "1 día a 3 meses" — ANTES de los patrones de días/meses
    (re.compile(r"1\s*d[\xeda]a\s*a\s*3\s*mes|1\s*day.*3\s*month", re.I), "1D-3M"),
    # 1 día
    (re.compile(r"(?<![2-9])1\s*d[\xeda]a(?!s)|1\s*day(?!s)|overnight", re.I), "1D"),
    # Días
    (re.compile(r"(?:30|31)\s*d[i\xeda]as?", re.I),   "1M"),
    (re.compile(r"(?:60|90)\s*d[i\xeda]as?", re.I),   "3M"),
    (re.compile(r"(?:150|180|237)\s*d[i\xeda]as?", re.I), "6M"),
    # Meses
    (re.compile(r"(?<![2-9])1\s*mes(?!es)", re.I),    "1M"),
    (re.compile(r"3\s*meses?|3\s*months?", re.I),    "3M"),
    (re.compile(r"6\s*meses?|6\s*months?|semestre", re.I), "6M"),
    (re.compile(r"12\s*meses?|12\s*months?", re.I),  "1Y"),
    # Menos de 1 año
    (re.compile(r"menos\s+de\s+1\s*a[\xf1n]|less\s+than\s+1\s*year", re.I), "<1Y"),
    # Años — sin \b para evitar corrupción, usar lookahead/lookbehind
    (re.compile(r"(?<![2-9])1\s*a[\xf1n]|(?<![2-9])1\s*years?", re.I),  "1Y"),
    (re.compile(r"(?<![3-9])2\s*a[\xf1n]|(?<![3-9])2\s*years?", re.I),  "2Y"),
    (re.compile(r"\(?3\s*a[\xf1n]|\(?3\s*years?", re.I),               "3Y"),
    (re.compile(r"(?<![3-9])4\s*a[\xf1n]|(?<![3-9])4\s*years?", re.I),  "4Y"),
    (re.compile(r"\(?5\s*a[\xf1n]|\(?5\s*years?", re.I),               "5Y"),
    (re.compile(r"[67]\s*a[\xf1n]|[67]\s*years?", re.I),                 "7Y"),
    (re.compile(r"[89]\s*a[\xf1n]|[89]\s*years?|10\s*a[\xf1n]|10\s*years?", re.I), "10Y+"),
]

def _detect_recommended_holding_period(text: str) -> Optional[str]:
    """
    Extrae y normaliza el período de mantenimiento recomendado.
    Devuelve código normalizado: "1D", "1M", "3M", "6M", "1Y", "3Y", "5Y", "10Y+", etc.
    Filtra texto narrativo y fechas de vencimiento capturadas por error.
    """
    if not text:
        return None
    m = _RHP_RE.search(text) or _RHP_EN_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".")

    # Rechazar texto narrativo o fechas
    if _RHP_REJECT_RE.search(raw):
        return None
    # Rechazar si no contiene ningún indicador de período
    if not _RHP_VALID_RE.search(raw):
        return None

    for pattern, code in _RHP_NORMALIZER:
        if pattern.search(raw):
            return code

    return None


# =================================================
# LEVERAGE_USED
# =================================================

def _detect_leverage(text: str) -> Optional[str]:
    """
    Detecta uso de apalancamiento desde el texto KIID/DDF.
    Devuelve 'YES', 'NO' o 'LIMITED'.
    """
    if not text:
        return None
    t = text.lower()

    if any(k in t for k in [
        "no utiliza apalancamiento", "no se utiliza apalancamiento",
        "no recurre al apalancamiento", "does not use leverage",
        "no apalancamiento", "sin apalancamiento",
    ]):
        return "NO"

    if any(k in t for k in [
        "apalancamiento limitado", "limited leverage",
        "apalancamiento máximo", "maximum leverage",
        "nivel de apalancamiento", "level of leverage",
        "hasta el 100%", "hasta el 200%",
    ]):
        return "LIMITED"

    if any(k in t for k in [
        "apalancamiento", "leverage", "endeudamiento financiero",
        "préstamos con fines de inversión",
    ]):
        return "YES"

    return None


# =================================================
# LIQUIDITY_PROFILE — días hábiles de rescate
# =================================================

_LIQUIDITY_RE = re.compile(
    r"(?:órdenes?\s+de\s+reembolso|redemption\s+orders?|reembolso)[^\.]{0,150}"
    r"(\d+)\s*d[íi]as?\s*h[áa]biles?",
    re.IGNORECASE | re.DOTALL
)

_LIQUIDITY_SAME_DAY = re.compile(
    r"valor\s+liquidativo\s+del\s+mismo\s+d[íi]a"
    r"|same\s+day\s+(?:nav|settlement)"
    r"|liquidaci[oó]n\s+en\s+el\s+d[íi]a",
    re.IGNORECASE
)

_LIQUIDITY_T1 = re.compile(
    r"siguiente\s+d[íi]a\s+h[áa]bil"
    r"|next\s+business\s+day"
    r"|d[íi]a\s+h[áa]bil\s+siguiente",
    re.IGNORECASE
)


def _detect_liquidity_profile(text: str) -> Optional[str]:
    """
    Detecta el perfil de liquidez (días hábiles hasta recibir el rescate).
    Devuelve 'T0', 'T1', 'T2', 'T5', 'T10+' o None.
    """
    if not text:
        return None

    if _LIQUIDITY_SAME_DAY.search(text):
        return "T0"

    if _LIQUIDITY_T1.search(text):
        return "T1"

    m = _LIQUIDITY_RE.search(text)
    if m:
        days = int(m.group(1))
        if days == 0:   return "T0"
        if days == 1:   return "T1"
        if days == 2:   return "T2"
        if days <= 5:   return "T5"
        if days <= 10:  return "T10+"
        return "T10+"

    return None


# =================================================
# DISTRIBUTION_FREQUENCY
# =================================================

# Patrones contextuales para Distribution_Frequency
# Requieren contexto explícito de reparto — evitan falsos positivos con
# "anual" / "semestral" en frases de costes o escenarios de rentabilidad
_DIST_FREQ_PATTERNS = [
    # ES: "El fondo reparte dividendos mensual/trimestral/..."
    (re.compile(r"reparte\s+dividendos?\s+(\w+(?:\s+\w+)?)", re.I),
     {"mensual":"MONTHLY","trimestral":"QUARTERLY","semestral":"BIANNUAL",
      "anual":"ANNUAL","anualmente":"ANNUAL","variable":"VARIABLE","discrecional":"VARIABLE"}),
    # ES: "distribución mensual/trimestral de dividendos/rentas"
    (re.compile(r"distribuci[oó]n\s+(mensual|trimestral|semestral|anual|mensuale?s|trimestrale?s)\s+"
                r"(?:de\s+)?(?:dividendos?|rentas?|ingresos?)", re.I),
     {"mensual":"MONTHLY","mensuales":"MONTHLY","trimestral":"QUARTERLY",
      "trimestrales":"QUARTERLY","semestral":"BIANNUAL","anual":"ANNUAL"}),
    # ES: "dividendos con carácter mensual/trimestral/..."
    (re.compile(r"dividendos?\s+con\s+car[aá]cter\s+(\w+)", re.I),
     {"mensual":"MONTHLY","trimestral":"QUARTERLY","semestral":"BIANNUAL","anual":"ANNUAL"}),
    # EN: "pays dividends monthly/quarterly/annually"
    (re.compile(r"pays?\s+(?:a\s+)?dividends?\s+(monthly|quarterly|semi.annually|annually|yearly)", re.I),
     {"monthly":"MONTHLY","quarterly":"QUARTERLY","semi-annually":"BIANNUAL",
      "semiannually":"BIANNUAL","annually":"ANNUAL","yearly":"ANNUAL"}),
    # EN: "distribution frequency: monthly/quarterly/..."
    (re.compile(r"distribution\s+frequency\s*[:\-]\s*(monthly|quarterly|semi.annual|annual)", re.I),
     {"monthly":"MONTHLY","quarterly":"QUARTERLY","semi-annual":"BIANNUAL","annual":"ANNUAL"}),
    # DDF: "El fondo reparte dividendos anual."
    (re.compile(r"reparte\s+dividendos\s+(mensual|trimestral|semestral|anual)", re.I),
     {"mensual":"MONTHLY","trimestral":"QUARTERLY","semestral":"BIANNUAL","anual":"ANNUAL"}),
]


def _detect_distribution_frequency(text: str, accumulation_policy: Optional[str]) -> Optional[str]:
    """
    Detecta la frecuencia de distribución usando patrones contextuales.

    Solo devuelve valor cuando:
    1. El texto contiene una frase explícita de reparto de dividendos/rentas
    2. Y la política de acumulación NO es ACCUMULATION

    Evita falsos positivos con keywords sueltos como "anual" o "semestral"
    que aparecen en secciones de costes o escenarios de rentabilidad.
    """
    if not text:
        return None
    if accumulation_policy == "ACCUMULATION":
        return None

    for pattern, freq_map in _DIST_FREQ_PATTERNS:
        m = pattern.search(text) or pattern.search(text.lower())
        if m:
            keyword = m.group(1).lower().rstrip(".")
            freq = freq_map.get(keyword)
            if freq:
                return freq

    return None
