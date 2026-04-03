# -*- coding: utf-8 -*-
"""
srri_text.py  — v3

Extractor SRRI – SOLO LÓGICA TEXTUAL

Cambios v3 (2026-03-07):
  FIX-FUSED-1   Nueva capa L0-FUSED antes del L0 estándar:
                  detecta "clasederiesgo([1-7])enunaescala" en texto OCR fusionado
                  (PDFs JPMorgan/Amundi de dos columnas donde el extractor elimina
                  todos los espacios entre palabras).
                  Cobertura empírica: 110/110 fondos con texto fusionado (Language=None).

  FIX-L0-1      Nuevos patrones declarativos PRIIP v3 / variantes regionales:
                  - "clasificado en la clase de riesgo N en una escala de 1 a 7"
                  - "se ha asignado la clase de riesgo N"
                  - "nivel de riesgo N [de 7]"
                  - "hemos asignado a este producto la clase de riesgo N"
                  - "the fund is in risk category N out of 7"   (EN)
                  - "this fund is classified as category N"     (EN)
                  - "ce produit a été classé N sur 7"           (FR)
                  - "este produto foi classificado na categoria N"  (PT)

  FIX-L1-1      Nuevos patrones L1 multilingüe:
                  - "risk class N" / "risk category N" (EN sin "de/sur 7")
                  - "klasse N" (DE/NL abreviado)
                  - "risiko N von 7" variante sin "klasse"

  FIX-CLEAN-1   Limpieza ampliada pre-extracción:
                  - Elimina "documento de datos fundamentales N / M" y
                    "document de données clés N / M" para evitar que el "1/3"
                    del encabezado page contamine como SRRI=1.
                  - Elimina también el artefacto "N de 7" cuando es parte de
                    "página N de 7" o "page N of 7".
                  - Elimina patrones de coste "N,XX %" y porcentajes de rentabilidad
                    que pueden dejar dígitos sueltos.

  FIX-CONF-1    Resolución de CONFLICT mejorada en resolve_srri_validation:
                  - Nuevo estado "VISUAL_SUSPICIOUS" cuando visual=1 y textual ∈ [2-7]
                    con confirmación L0: la gran mayoría de vis=1 CONFLICTs son falsos
                    positivos geométricos (ancla en "1/3" de encabezado).
                  - La calidad pasa a LOW_CONFLICT → MEDIUM_TEXT en ese caso.

Contrato externo mantenido (misma firma de extract_srri y resolve_*).
"""

from typing import Optional, Dict, Tuple
import re
import fitz  # PyMuPDF

# ============================================================
# PARÁMETROS CANÓNICOS DEL MÓDULO
# ============================================================

TEXT_CONTEXT_WINDOW = 300


# ============================================================
# FUNCIONES CANÓNICAS DE RESOLUCIÓN
# ============================================================

def resolve_srri_validation(
    srri_visual: Optional[int],
    srri_textual: Optional[int],
    textual_level: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Determina el estado de validación y el indicador de calidad.

    v3: cuando visual=1 y textual ∈ [2-7] confirmado por L0/L0-FUSED,
    el visual es casi siempre un falso positivo del módulo geométrico
    (el "1" proviene del encabezado "1/3" de página, no del widget SRRI).
    En ese caso se devuelve VISUAL_SUSPICIOUS en lugar de CONFLICT,
    y la calidad se eleva a MEDIUM_TEXT para que el pipeline use el textual.
    """
    if srri_visual is None and srri_textual is None:
        return "NOT_AVAILABLE", "NONE"

    if srri_visual is not None and srri_textual is not None:
        if srri_visual == srri_textual:
            return "MATCH", "HIGH"
        # FIX-CONF-1: vis=1 casi siempre es ruido del encabezado de página
        if srri_visual == 1 and srri_textual in range(2, 8):
            if textual_level in ("L0", "L0_FUSED"):
                return "VISUAL_SUSPICIOUS", "MEDIUM_TEXT"
        return "CONFLICT", "LOW_CONFLICT"

    if srri_visual is not None:
        return "VISUAL_ONLY", "MEDIUM_VISUAL"

    return "TEXT_ONLY", "MEDIUM_TEXT"


def resolve_srri_source(
    srri_visual: Optional[int],
    srri_textual: Optional[int],
) -> str:

    if srri_visual is not None and srri_textual is not None:
        return "BOTH_MATCH" if srri_visual == srri_textual else "BOTH_CONFLICT"

    if srri_textual is not None:
        return "TEXTUAL"

    if srri_visual is not None:
        return "VISUAL"

    return "NONE"


# ============================================================
# API PÚBLICA
# ============================================================

def extract_srri(
    pdf_bytes: bytes,
    isin: Optional[str] = None
) -> Dict[str, Optional[object]]:

    scanner = _SRRIScanner()
    srri_textual, textual_level = scanner.extract_by_text_v3(pdf_bytes)

    # Visual eliminado completamente (se mantiene None para que el módulo geométrico
    # lo llene externamente si está activo)
    srri_visual = None

    validation_status, quality_flag = resolve_srri_validation(
        srri_visual,
        srri_textual,
        textual_level,
    )

    srri_source = resolve_srri_source(srri_visual, srri_textual)
    final_srri = srri_textual

    return {
        "SRRI": final_srri,
        "SRRI_Visual": srri_visual,
        "SRRI_Textual": srri_textual,
        "SRRI_Textual_Level": textual_level,   # nuevo campo de trazabilidad
        "SRRI_Source": srri_source,
        "SRRI_Validation_Status": validation_status,
        "SRRI_Quality_Flag": quality_flag,
    }


# ============================================================
# IMPLEMENTACIÓN INTERNA
# ============================================================

class _SRRIScanner:

    def extract_by_text_v3(
        self,
        pdf_bytes: bytes,
    ) -> Tuple[Optional[int], Optional[str]]:
        """
        Extrae el SRRI del PDF por análisis textual.
        Devuelve (valor, nivel_de_confianza) o (None, None).
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            text = " ".join(
                page.get_text()
                for i, page in enumerate(doc)
                if i < 2
            )
            doc.close()
        except Exception:
            return None, None

        if not text:
            return None, None

        text = text.lower()
        text = re.sub(r"\s+", " ", text)

        # ── Limpieza pre-extracción (FIX-CLEAN-1) ──────────────────────────────

        # Encabezados de página: "1 / 3", "documento de datos fundamentales 1 / 3"
        # Esta es la causa principal del falso positivo visual=1 en el módulo geométrico,
        # y también puede afectar al L2 de texto.
        text = re.sub(
            r"(?:documento\s+de\s+datos\s+fundamentales|document\s+de\s+donn[eé]es\s+cl[eé]s|"
            r"document\s+d['']\s*informations\s+cl[eé]s|key\s+(?:investor\s+)?(?:information\s+)?document)\s*"
            r"\d+\s*[/\\]\s*\d+",
            " ",
            text,
        )
        text = re.sub(
            r"(page|pagina|página|seite)\s*\d+\s*(of|de|von|/)\s*\d+",
            " ",
            text,
        )

        # Años y horizontes temporales
        text = re.sub(r"\b(19|20)\d{2}\b", " ", text)
        text = re.sub(r"\b\d+\s*(año|años|year|years)\b", " ", text)
        text = re.sub(r"\b\d+\s*(an|ans|jahr|jahre)\b", " ", text)

        # Porcentajes de rentabilidad y costes ("4,35 %", "-2 %") para evitar
        # que el dígito aislado antes de "%" contamine el L2
        text = re.sub(r"-?\d+[,\.]\d+\s*%", " ", text)
        text = re.sub(r"-?\d+\s*%", " ", text)

        # ── L0-FUSED: texto OCR sin espacios (FIX-FUSED-1) ────────────────────
        # PDFs JPMorgan/Amundi con palabras fusionadas: las líneas se concatenan
        # sin separador, produciendo "...clasificadoesteproductoenlaclasederiesgo4enunaescala..."
        # Se trabaja sobre el texto sin espacios para encontrar el patrón.
        text_fused = text.replace(" ", "")

        _FUSED_PATTERNS = [
            # "clasederiesgoNenunaescala" — el más frecuente (110/110 en corpus)
            r"clasederiesgo([1-7])enunaescala",
            # Variante sin "una": "clasederiesgoNenescala"
            r"clasederiesgo([1-7])enescalade",
            # "laclasederiesgoN" con cierre de frase
            r"laclasederiesgo([1-7])[,\.\s]",
            # "clasificationN" estilo Amundi
            r"clasificaci[oó]n\s*([1-7])\s*de\s*7",
        ]
        for rx in _FUSED_PATTERNS:
            hits = [int(m.group(1)) for m in re.finditer(rx, text_fused)]
            if len(set(hits)) == 1:
                return hits[0], "L0_FUSED"
            elif len(set(hits)) > 1:
                # Múltiples valores distintos en texto fusionado → no fiable
                break

        # ── L0: patrones declarativos (máxima fiabilidad) ─────────────────────
        lvl0_patterns = [

            # ── Español ────────────────────────────────────────────────────────
            # PRIIP v2/v3: "hemos clasificado este producto en la clase de riesgo N"
            r"hemos\s+clasificado\s+este\s+(?:producto|fondo|subfondo)\s+en\s+la\s+(?:clase\s+de\s+riesgo|categor[ií]a)\s+([1-7])",
            # PRIIP alternativo: "hemos clasificado este producto como N de 7"
            r"hemos\s+clasificado\s+este\s+(?:producto|fondo|subfondo)\s+como\s+([1-7])\s+(?:de\s+|en\s+una\s+escala\s+de\s+)7",
            # Variante cartera/solución
            r"hemos\s+clasificado\s+esta\s+(?:cartera|soluci[oó]n)\s+en\s+la\s+clase\s+de\s+riesgo\s+([1-7])",
            # FIX-L0-1: "hemos asignado a este producto la clase de riesgo N"
            r"hemos\s+asignado\s+(?:a\s+este\s+(?:producto|fondo|subfondo)\s+)?la\s+clase\s+de\s+riesgo\s+([1-7])",
            # FIX-L0-1: "se ha asignado la clase de riesgo N"
            r"se\s+ha\s+asignado\s+(?:la\s+clase\s+de\s+riesgo|la\s+categor[ií]a\s+de\s+riesgo)\s+([1-7])",
            # "está clasificado en el nivel N de 7"
            r"est[aá]\s+clasificad[ao]\s+en\s+el\s+(?:nivel|clase)\s+([1-7])\s+(?:de\s+7|en\s+una)",
            r"la\s+cartera\s+est[aá]\s+clasificada\s+en\s+el\s+(?:nivel|clase)\s+([1-7])\s+de\s+7",
            # FIX-L0-1: "clasificado en la clase de riesgo N en una escala de 1 a 7"
            r"clasificad[ao]\s+en\s+la\s+(?:clase\s+de\s+riesgo|categor[ií]a)\s+([1-7])\s+en\s+una\s+escala",
            # "en la clase N de 7" (PDF de dos columnas, frase incompleta)
            r"en\s+la\s+clase\s+([1-7])\s+de\s+7[,\s]",
            # "N de 7, que es [la clase de riesgo|un riesgo]"
            r"\b([1-7])\s+de\s+7,\s+que\s+es\s+(?:la\s+clase\s+de\s+riesgo|un\s+riesgo)",
            # "de riesgo N en una escala de 7"
            r"de\s+riesgo\s+([1-7])\s+en\s+una\s+escala",
            # FIX-L0-1: "nivel de riesgo N de 7" / "nivel de riesgo: N"
            r"nivel\s+de\s+riesgo\s+([1-7])\s+(?:de\s+7|en\s+una\s+escala)",
            r"nivel\s+de\s+riesgo\s*[:=]\s*([1-7])\b",

            # ── Inglés ─────────────────────────────────────────────────────────
            # FIX-L0-1: "the fund is in risk category N out of 7"
            r"(?:the\s+)?(?:fund|product|sub-?fund)\s+is\s+(?:in\s+)?risk\s+(?:class|category)\s+([1-7])\s+(?:out\s+of\s+7|of\s+7)",
            # FIX-L0-1: "this fund is classified as category N"
            r"(?:this\s+)?(?:fund|product)\s+is\s+classified\s+(?:as\s+)?(?:risk\s+)?(?:class|category)\s+([1-7])",
            # "we have classified this product as N out of 7"
            r"we\s+have\s+classified\s+this\s+(?:product|fund)\s+(?:as\s+)?(?:class\s+)?([1-7])\s+(?:out\s+of\s+7|of\s+7)",
            # "risk class N on a scale of 1 to 7"
            r"risk\s+class\s+([1-7])\s+(?:on\s+a\s+scale|out\s+of)",

            # ── Francés ────────────────────────────────────────────────────────
            # FIX-L0-1: "ce produit a été classé N sur 7"
            r"ce\s+(?:produit|fonds?)\s+a\s+[eé]t[eé]\s+class[eé]\s+([1-7])\s+sur\s+7",
            r"nous\s+avons\s+class[eé]\s+ce\s+(?:produit|fonds?)\s+(?:en\s+)?(?:cat[eé]gorie\s+)?([1-7])",
            # "en classe de risque N sur 7"
            r"en\s+classe\s+de\s+risque\s+([1-7])\s+sur\s+7",
            r"class[eé]\s+([1-7])\s+sur\s+7[,\s]",

            # ── Portugués ──────────────────────────────────────────────────────
            # FIX-L0-1: "este produto foi classificado na categoria N"
            r"(?:este\s+produto|este\s+fundo)\s+(?:foi\s+)?classificad[ao]\s+na\s+(?:categor[ií]a|classe)\s+de\s+risco\s+([1-7])",

            # ── Italiano ───────────────────────────────────────────────────────
            r"questo\s+(?:prodotto|fondo)\s+[eè]\s+(?:stato\s+)?classificat[oa]\s+nella\s+(?:classe|categoria)\s+di\s+rischio\s+([1-7])",
            r"abbiamo\s+classificato\s+questo\s+(?:prodotto|fondo)\s+(?:nella\s+)?(?:classe\s+)?([1-7])",
        ]

        vals = []
        for rx in lvl0_patterns:
            for m in re.finditer(rx, text):
                vals.append(int(m.group(1)))

        if len(set(vals)) == 1:
            return vals[0], "L0"
        elif len(set(vals)) > 1:
            # Múltiples L0 con valores distintos → ambiguo, no fiable
            return None, "L0_CONFLICT"

        # ── L1: patrones de alta fiabilidad ───────────────────────────────────
        lvl1_patterns = [
            # Español
            r"indicador\s+sint[eé]tico\s+de\s+riesgo\s+(es|:)\s*([1-7])",
            r"categor[ií]a\s+([1-7])\s+de\s+7",
            # Inglés
            r"summary\s+risk\s+indicator\s+(?:is|:)\s*([1-7])",
            # FIX-L1-1: "risk class N" / "risk category N" sin calificador "de 7"
            r"\brisk\s+(?:class|category)\s+([1-7])\b",
            # Francés
            r"indicateur\s+synth[eé]tique\s+de\s+risque\s+(?:est|:)\s*([1-7])",
            r"cat[eé]gorie\s+([1-7])\s+sur\s+7",
            # FIX-L1-1: "catégorie N" sin "sur 7" (Amundi, BNP)
            r"cat[eé]gorie\s+de\s+risque\s+([1-7])\b",
            # Alemán / holandés
            r"risikoklasse\s+([1-7])\s+von\s+7",
            # FIX-L1-1: "Risikoklasse N" sin "von 7" / "klasse N"
            r"risikoklasse\s+([1-7])\b",
            r"\brisikokategorie\s+([1-7])\b",
            # FIX-L1-1: "risiko N von 7" (variante sin "klasse")
            r"\brisiko\s+([1-7])\s+von\s+7\b",
        ]

        vals = []
        for rx in lvl1_patterns:
            for m in re.finditer(rx, text):
                digit = m.group(m.lastindex)
                vals.append(int(digit))

        if len(set(vals)) == 1:
            return vals[0], "L1"
        elif len(set(vals)) > 1:
            return None, "L1_CONFLICT"

        # ── L2: contexto por palabra clave SRRI ───────────────────────────────
        srri_keywords = [
            "srri",
            "synthetic risk",
            "summary risk",
            "indicador sint",
            "indicateur synth",
            "risiko",
            "risikoindikator",
        ]

        best_digit = None
        best_conf = 0.0

        for kw in srri_keywords:
            idx = text.find(kw)
            if idx == -1:
                continue

            snippet = text[idx: idx + TEXT_CONTEXT_WINDOW]

            for m in re.finditer(r"\b([1-7])\b", snippet):
                digit = int(m.group(1))
                distance = m.start()
                conf = 1.0 / (1.0 + distance / 50.0)
                if conf > best_conf:
                    best_conf = conf
                    best_digit = digit

        if best_digit is not None and best_conf > 0.3:
            return best_digit, "L2"

        return None, None

    # ── Compatibilidad con v2 ─────────────────────────────────────────────────
    def extract_by_text_v4_optimal(
        self,
        pdf_bytes: bytes,
    ) -> Optional[int]:
        """Alias de compatibilidad con el contrato v2 (devuelve solo el valor)."""
        val, _ = self.extract_by_text_v3(pdf_bytes)
        return val
