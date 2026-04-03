# srri_v16_geometric.py  (srri_v15_geometric base + V16-FIX-1/MAX_BAND_ITER)
#
# [V16-FIX-1 / MAX_BAND_ITER] Límite de iteraciones en band-scan OCR
#   Fondos afectados: PDFs con layout 2 columnas (Edmond de Rothschild, algunos JPM)
#   Causa raíz: en PDFs de 2 columnas, _find_digit_row_from_pdf rechaza el cluster
#   (CV > 0.5) pero pdf_layer_absent=False → band-scan corre completo (~25-37
#   iteraciones Tesseract, ~18-26s) sin posibilidad de éxito porque los dígitos
#   del widget y del texto de la columna derecha están entremezclados.
#   Fix: añadir _MAX_BAND_ITER=15 en _detect_srri_in_roi. Tras 15 iteraciones
#   sin resultado, abortar el band-scan y pasar al no-OCR final.
#   Impacto: 25-37 iter → máx 15 iter = ~10s por ROI en lugar de ~18-26s.
#   PDFs que resuelven en las primeras iteraciones: sin cambio (early-exit).
#
# srri_v15_geometric.py  (srri_v14_geometric base + FIX-BLOB-ROBECO + FIX-DUP-ROI)
#
# [V15-FIX-1 / FIX-BLOB-ROBECO] Detección de widget con borde compuesto de asp alto
#   Fondos afectados: Robeco (LU0128640439 y toda la gama del gestor).
#   Causa raíz: en los KIIDs de Robeco, los bordes teal de las 7 celdas forman
#   UNA SOLA región conectada en el threshold BINARY_INV (borde exterior + los
#   6 divisores internos son contiguos). El bounding-box resultante tiene:
#     w ≈ 1111px (89% del ancho de página a 150dpi)
#     h ≈ 35px
#     asp ≈ 31.7
#   Este blob pasa ambos filtros de FIX-BLOB-1 (asp 5.0-9.5) sin matchear:
#     • No entra en cells_nm porque asp > 10 (filtro inicial por contorno)
#     • No entra en blob_candidates porque asp > 9.5
#   Consecuencia: _detect_widget_no_ocr retorna None para ambos nm_thresh,
#   los pases morph también fallan (misma causa), y el pre-check V14 devuelve
#   None → band-scan OCR ejecuta completamente (~17-26 llamadas Tesseract,
#   ~10-20s) sin encontrar nada útil. Resultado: no-SRRI + tiempo excesivo.
#
#   Fix: dentro del bloque `if len(cells_nm) < 5`, DESPUÉS del FIX-BLOB-1,
#   escanear cnts_nm DIRECTAMENTE (antes del filtro asp<=10) buscando blobs
#   con asp ∈ [9.0, 42.0] y w > 20% del ancho del ROI. Este rango cubre todos
#   los widgets de 7 celdas que abarcan el ancho de la columna de texto:
#     • asp mínimo (celdas cuadradas): 7 × 1.0 = 7 (imposible en la práctica)
#     • asp típico Robeco: 1111/35 = 31.7
#     • asp máximo estimado: ≈ 42 (página estrecha + celdas altas)
#   El blob detectado se subdivide en 7 columnas iguales y se puntúa con
#   _score_cells_grid_aligned. La señal de color es clarísima:
#     sat celda 4 = 232.2  vs  sat otras = 22-27  →  z = 2.45, dom = 2.82 ✓
#   Via V14 pre-check, el SRRI se resuelve antes del band-scan: 0 llamadas
#   Tesseract adicionales. Tiempo estimado: ~20-30s → ~1-2s.
#
# [V15-FIX-2 / FIX-DUP-ROI] Eliminar slices duplicados en extract()
#   Causa raíz: cuando anchor_y_px + 0.60 × h_full > h_full (la ventana s0
#   excede el fondo de la página, como ocurre con Robeco donde anchor ≈ 66%
#   de la página), las slices s0 y s2 producen bounds idénticos:
#     s0 = (ay_px, h_full)  =  s2 = (ay_px, h_full)
#   El mismo ROI se procesa dos veces, doblando OCR fallback y Tesseract calls.
#   Fix: en el bucle de slices, registrar bounds vistos y saltar duplicados.
#   Sin impacto en fondos con anchor en la mitad superior de la página.
#
# srri_v14_geometric.py  (srri_v13_geometric base + FIX-NOOCR-FIRST)
#
# [V14-FIX-1 / FIX-NOOCR-FIRST] Pre-check no-OCR ANTES del band-scan
#   Fondos afectados: BlackRock IE00B45H7020 (22s→~2s),
#                     Groupama FR0000989626 / FR0013296332 (14-15s→~2s).
#   Causa raíz: ambos tienen widget raster en PDF con capa de texto.
#   • pdf_layer_absent=False → band-scan ejecuta ~17-26 llamadas Tesseract
#     (~12-18s) antes de llegar a _detect_widget_no_ocr (puro OpenCV, <0.1s).
#   • El no-OCR resuelve en 2 passes con early-exit (no-morph thresh=128+160).
#   Fix: en _detect_srri_in_roi, llamar _detect_widget_no_ocr ANTES del
#   band-scan (PASO 2c-PRE). Si retorna resultado → return inmediato.
#   Si retorna None (incertidumbre) → band-scan corre inalterado.
#   Cero regresiones: fondos que NECESITAN band-scan (widget vectorial,
#   OCR necesario) tienen no-OCR=None y continúan exactamente igual.
#
# Incluye también FIX-BLOB-1 (heredado de v13):
#
#   Causa raíz: el widget PRIIP tiene 7 celdas sin separadores oscuros visibles.
#   En BINARY_INV (thresh=128), RETR_EXTERNAL detecta el widget entero como
#   un único rectángulo de aspect ratio ≈ 7 (e.g. 568×79px). El bucle no-morph
#   recibe cells_nm con 1-4 items → "len < 5 → continue" → nunca puntúa → falla.
#   Fix: cuando len(cells_nm) < 5, buscar blobs con asp ∈ [5.0, 9.5], dividir
#   en 7 columnas iguales, y puntuar con _score_cells_grid_aligned (z≥2.0 filtra
#   falsos positivos). El z_sat del blob real es 2.45 (célula 1 naranja vs gris).
#   Impacto rendimiento: early exit en thresh=128+160 → 0 llamadas OCR
#   adicionales. Tiempo Groupama: ~144s → ~4s.
#
# OPTIMIZACIONES DE RENDIMIENTO SOBRE V11
# Causa raíz: tiempo de proceso excesivo en HSBC (38 min), Allianz (13.7 min),
# JPMorgan (12.9 min), BlackRock (67s), Goldman Sachs (27s).
#
# ═══════════════════════════════════════════════════════════════════════════
# CAUSAS RAÍZ DE LENTITUD (diagnóstico log70 + debug_70):
# ═══════════════════════════════════════════════════════════════════════════
#
# R1. DPI=300 genera imágenes 2481×3508px (8.7 Mpx).
#     _render_page tarda ~12s/página. Toda op OpenCV/Tesseract es 4-16× lenta.
#     Diagnóstico: Goldman Sachs 27s → 12s solo en render. Allianz 24s → ídem.
#
# R2. Band-scan (PASO 2c) itera (h_roi-200)/50 ≈ 66 Tesseract calls por ROI.
#     A 300dpi: 66 × ~0.3s = ~20s. Útil solo para PDFs sin capa de texto.
#     Para BlackRock (widget raster, sin capa texto) desperdicia 20s antes
#     de llegar al no-OCR que sí funciona.
#
# R3. _detect_widget_no_ocr ejecuta 14 variantes sin salida temprana.
#     Las variantes no-morph [V11-FIX-IE00] ocupan posición 13-14 pero son
#     las únicas que funcionan para KIIDs PRIIP-v3 modernos (BlackRock, Amundi).
#     Amundi: pass #1 ya encuentra SRRI=4, pero ejecuta 9 passes más.
#     BlackRock: passes 1-12 fallan silenciosamente, pass 13 (no-morph) funciona.
#
# R4. Re-render doble: si PASO 0 falla, PASO 1+ re-renderiza las mismas páginas.
#
# ═══════════════════════════════════════════════════════════════════════════
# [V12-FIX-1] DPI_DEFAULT 300 → 150  (impacto: ×4 en render y OCR)
#   • _render_page: ~3s/pág vs ~12s/pág (Goldman Sachs, Allianz: 27s→5s)
#   • Tesseract en ROI completo: ~0.7s vs ~3s
#   • Band-scan: 31 bandas vs 66, cada una ×4 más rápida
#   • no-OCR contour detection: ~0.5s/variante vs ~2s
#   Compatibilidad:
#   • PDF digit row: scale=150/72=2.08, dígitos ~19px, pasa filtro h>5 ✓
#   • Band-scan 2× upscale: 150dpi×2=300dpi efectivos, idéntica calidad ✓
#   • Color scoring: suficiente a cualquier DPI ✓
#
# [V12-FIX-2] _detect_widget_no_ocr: no-morph PRIMERO + early exit
#   • No-morph passes (sin morphologyEx) pasan a posición 1-2.
#   • El bucle morph normal (3 variantes × 4 thresh = 12 passes) pasa al final.
#   • Early exit: en cuanto `results` tiene 2 valores iguales → return inmediato.
#   • Impacto BlackRock: de 14 passes a 2 (no-morph thresh=128 → SRRI=4).
#   • Impacto Amundi: de 10 passes a 2 (no-morph thresh=128 → SRRI=4).
#   • Impacto IE00B45H7020: sin cambio (no-morph ya era el path correcto).
#
# [V12-FIX-3] Flag pdf_layer_absent → skip band-scan
#   • Si _find_digit_row_from_pdf devuelve None en TODAS las páginas, el widget
#     es probablemente raster. El band-scan (OCR) nunca encontrará dígitos.
#     Se marca pdf_layer_absent=True y se salta PASO 2c en _detect_srri_in_roi.
#   • Impacto BlackRock: ahorra ~20s de band-scan inútil.
#
# [V12-FIX-4] Cache de renders en extract()
#   • Dict page_cache[page_idx] → np.ndarray. Evita render doble si PASO 0
#     falla y PASO 1+ necesita la misma página.
#
# [V12-FIX-5] _score_cells_grid_aligned: alineación de grid para n < 7 celdas
#   • _estimate_digit (usado cuando n<7) interpola fracionalmente asumiendo que
#     la primera celda detectada es la posición 1. Error si faltan celdas por la
#     izquierda (celda oscura no binarizada a ese umbral).
#   • Ejemplo BlackRock LU0171275786: thresh=128 detecta celdas 2-7 (6 celdas).
#     _estimate_digit → SRRI=3 (incorrecto). Fix → SRRI=4 ✓
#   • Prueba todos los k=0..(7-n) desplazamientos. Prefiere alineación donde
#     las 7 posiciones caben en la imagen (in_bounds×10 + z_winner).
#   • Sustituye a _score_cells_v6 en los paths no-morph y morph de no-OCR.
#
# [V12-FIX-6] Rechazo de cluster PDF con x_centers colapsados
#   • En KIIDs a dos columnas (JPMorgan LU0982976267), los dígitos del widget
#     (columna izquierda) aparecen en la capa de texto con el mismo x_center
#     para varios valores (val=1,2,4 todos en x=878px). min_gap=0.
#   • _find_digit_row_from_pdf aceptaba este cluster (n_unique≥4 pero CV=1.51).
#   • _score_with_ocr_grid con polyfit m≈0 extrapolaba todas las posiciones al
#     mismo x → z_sat sin outlier claro → winner=0 → SRRI=1 siempre incorrecto.
#   • Fix: rechazar cluster si min_gap < 0.5×med_h_pts O CV > 0.5.
#   • Aplicado en _find_digit_row_from_pdf Y en _detect_srri_via_pdf_row.
#   • Con cluster rechazado, el pipeline pasa a no-OCR (correcto para este fondo).
#
# [V12-FIX-7] Invertir orden PASO 2a / PASO 2b en _detect_srri_in_roi
#   • Antes: PASO 2a (cell detection) → PASO 2b (OCR+grid).
#   • Problema KIIDs dos columnas (LU0982976267): strip de 1241px incluye
#     columna derecha. La morfo-close de _find_cells_in_strip fusiona celdas
#     del widget con contornos del texto derecho. Resultado: celda naranja
#     (SRRI=4) aparece en índice 1 de la row → _score_cells_v6 → SRRI=2.
#   • OCR+grid: vals=[1,2,5,6,7], polyfit m=77.6, val=4→cx=331, sat=146 → SRRI=4 ✓
#   • Fix: OCR+grid PRIMERO. Cell detection queda como fallback si grid falla.
#
# [V12-FIX-8] RANSAC-lite en _score_with_ocr_grid: rechazar outliers pre-polyfit
#   • Problema: Tesseract (Windows en particular) puede devolver falsos positivos
#     para un dígito cuyo val ya existe en el cluster correcto. Ejemplo log82:
#     val=3 x=59 y=59 w=1106 h=15 x_center=612 (bloque de texto completo de la
#     columna derecha, mal clasificado como '3'). Con tol laxa (med_h alto por
#     muchos dígitos de texto en la página) este falso positivo se cuela en
#     digit_row. polyfit incluye (val=3, cx=612) junto a (val=1,2,5,6,7) →
#     m=64.84, val=3→cx=326 (justo en la celda naranja) → winner=idx2 → SRRI=3.
#   • Fix: fit inicial → residuales → descartar outliers con residual>1.5×cell_w.
#     Aplicar solo si quedan ≥3 inliers. Luego refit con inliers.
#   • Residual val=3@cx=612: 286px >> threshold≈83px → descartado ✓
#   • Refit con [1,2,5,6,7]: m=77.66 → val=4→cx=331 → sat=147 → SRRI=4 ✓
#
# [V11-FIX-IE00] Pase sin morphologyEx para IE00B45H7020.
# [V9-FIX-1]    Capa de texto PDF como fuente primaria.
# [V8-FIX-*]    Multi-slice ROI, hue divergence, CLAHE retry.
# [V7-FIX-*]    Anchor estricto, padding izquierdo ×4, V7-FIX-3 zscore.
# [V6-FIX-*]    Scoring sobre ROI completo.
# ═══════════════════════════════════════════════════════════════════════════

import os
import cv2
import fitz
import json
import numpy as np
import pytesseract
from typing import Optional, List, Tuple, Dict
import unicodedata
import re

DEBUG = False
DEBUG_ROOT = "c:\\data\\fondos\\debug"

TESS_CONFIG = "--psm 6 -c tessedit_char_whitelist=1234567"

DPI_DEFAULT = 150  # [V12-FIX-1] era 300 → ×4 speedup en render y OCR


def _pdf_pts_to_px(y_pts: float, dpi: int) -> int:
    """Convierte coordenada Y en puntos PDF (@ 72dpi) a píxeles a DPI dado."""
    return int(y_pts * dpi / 72.0)


class SRRIV4Geometric:
    """
    Interfaz pública idéntica a versiones anteriores.
    Nombre de clase mantenido por compatibilidad con el pipeline externo.
    """

    def __init__(self, isin: Optional[str] = None, dpi: int = DPI_DEFAULT):
        self.isin = isin or "UNKNOWN"
        self.dpi = dpi
        self._ts = ""
        self.debug_dir = os.path.join(DEBUG_ROOT, self.isin)
        if DEBUG:
            os.makedirs(self.debug_dir, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────
    # ENTRY POINT
    # ─────────────────────────────────────────────────────────────────

    def extract(self, pdf_bytes: bytes) -> Optional[int]:
        import time
        self._ts = str(int(time.time() * 1000))
        self.debug_dir = os.path.join(DEBUG_ROOT, self.isin, self._ts)
        if DEBUG:
            os.makedirs(self.debug_dir, exist_ok=True)

        print(">>> SRRI V12 RUNNING")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # [V12-FIX-4] Cache de renders: evita re-renderizar la misma página
        # en PASO 0 y PASO 1+. Dict {page_idx: np.ndarray}.
        _page_cache: Dict[int, np.ndarray] = {}

        def _get_page_img(pidx: int) -> np.ndarray:
            if pidx not in _page_cache:
                _page_cache[pidx] = self._render_page(doc[pidx])
            return _page_cache[pidx]

        # ── [V9-FIX-1] PASO 0: Capa de texto PDF ─────────────────────────
        # Intenta localizar el widget leyendo las posiciones exactas de los
        # dígitos 1-7 directamente del PDF vectorial (sin Tesseract).
        # Se itera página por página porque el widget puede estar en pág 0, 1 ó 2.
        # [V12-FIX-3] Registrar si la capa de texto está ausente en TODAS las páginas.
        # [V12-FIX-6c] Distinguir dos casos:
        #   A) PDF raster (sin capa de texto alguna) → band-scan inútil → desactivar
        #   B) PDF con texto pero cluster de dígitos rechazado → band-scan SIGUE activo
        # Para (A): comprobar si la página tiene ALGÚN carácter, no solo la fila de dígitos.
        _pages_with_any_text = 0    # (A) páginas con capa de texto
        _pages_with_pdf_layer = 0   # páginas con fila de dígitos 1-7 válida
        for page_idx in range(min(3, len(doc))):
            page = doc[page_idx]
            # [V12-FIX-6c] Detectar presencia de texto aunque no haya fila de dígitos válida
            try:
                _raw_check = page.get_text("rawdict")
                if any(block.get("type") == 0 for block in _raw_check.get("blocks", [])):
                    _pages_with_any_text += 1
            except Exception:
                pass
            digit_row_pdf = self._find_digit_row_from_pdf(page)
            if digit_row_pdf is None:
                continue
            _pages_with_pdf_layer += 1
            full_img = _get_page_img(page_idx)     # [V12-FIX-4] usa cache
            if DEBUG:
                cv2.imwrite(os.path.join(self.debug_dir,
                            f"p{page_idx}_full.png"), full_img)
                vals_found = [d["val"] for d in
                              sorted(digit_row_pdf, key=lambda x: x["x_center"])]
                print(f"[V12] page={page_idx} PDF digit_row={vals_found}")
            winner = self._score_with_ocr_grid(full_img, digit_row_pdf)
            if winner is not None:
                if DEBUG:
                    print(f"[V12] page={page_idx} RESULT via PDF+grid: {winner}")
                doc.close()
                return winner

        # [V12-FIX-3/6c] pdf_layer_absent=True SOLO si el PDF es completamente raster
        # (sin capa de texto). Si hay texto pero el cluster fue rechazado (layout
        # a columnas, etc.), el band-scan OCR sigue siendo útil.
        _pdf_layer_absent = (_pages_with_any_text == 0)
        if DEBUG and _pdf_layer_absent:
            print(f"[V12] pdf_layer_absent=True → band-scan desactivado")

        # ── PASO 1+: Fallback V8 (anchor + slices + OCR + no-OCR) ─────────
        pages_with_anchor: List[Tuple[int, float]] = []
        pages_without_anchor: List[Tuple[int, None]] = []

        for page_idx in range(min(3, len(doc))):
            page = doc[page_idx]
            ay = self._find_anchor(page)
            if ay is not None:
                pages_with_anchor.append((page_idx, ay))
            else:
                pages_without_anchor.append((page_idx, None))

        ordered = pages_with_anchor + pages_without_anchor

        for page_idx, anchor_y in ordered:
            page = doc[page_idx]
            full_img = _get_page_img(page_idx)     # [V12-FIX-4] usa cache

            if DEBUG:
                cv2.imwrite(os.path.join(self.debug_dir, f"p{page_idx}_full.png"), full_img)
                print(f"[V12] page={page_idx}  anchor_pts={anchor_y}")

            # [V9-FIX-PDF] Intentar primero la localización nativa via PyMuPDF
            pdf_result = self._detect_srri_via_pdf_row(page, full_img)
            if pdf_result is not None:
                if DEBUG:
                    print(f"[V11] page={page_idx} RESULT via PDF-row: {pdf_result}")
                doc.close()
                return pdf_result

            rois_to_try = []

            if anchor_y is not None:
                ay_px = _pdf_pts_to_px(anchor_y, self.dpi)
                h_full = full_img.shape[0]

                # [V8-FIX-A] Múltiples ROIs desde el anchor hacia abajo.
                # Cubre casos donde el widget está al pie de la misma página
                # (anchor en Y=226, widget en Y=85% de la página).
                # Probamos 3 ventanas solapadas:
                #   s0: anchor → anchor+60% (ROI original aprox)
                #   s1: 40% de la página → fondo (captura widgets al pie)
                #   s2: anchor → fondo (ventana larga completa)
                slices = [
                    (max(0, ay_px), min(h_full, ay_px + int(h_full * 0.60))),
                    (int(h_full * 0.40), h_full),
                    (max(0, ay_px), h_full),
                ]
                saved_roi = False
                _seen_bounds: set = set()   # [V15-FIX-2] deduplicar slices
                for si, (y1, y2) in enumerate(slices):
                    if y2 - y1 < 50:
                        continue
                    # [V15-FIX-2 / FIX-DUP-ROI] Cuando anchor_y_px + 0.60×h_full > h_full,
                    # s0 y s2 producen bounds idénticos → mismo ROI procesado dos veces.
                    _bounds_key = (y1, y2)
                    if _bounds_key in _seen_bounds:
                        continue
                    _seen_bounds.add(_bounds_key)
                    roi_slice = full_img[y1:y2, :]

                    if DEBUG and not saved_roi:
                        cv2.imwrite(os.path.join(self.debug_dir, f"p{page_idx}_roi.png"), roi_slice)
                        dbg = full_img.copy()
                        cv2.line(dbg, (0, ay_px), (dbg.shape[1], ay_px), (0, 0, 255), 2)
                        cv2.imwrite(os.path.join(self.debug_dir, f"p{page_idx}_anchor.png"), dbg)
                        saved_roi = True
                    rois_to_try.append((roi_slice, f"p{page_idx}_s{si}"))

            # Siempre añadir la imagen completa como fallback
            rois_to_try.append((full_img, f"p{page_idx}_full"))

            for roi, tag in rois_to_try:
                result = self._detect_srri_in_roi(roi, tag=tag,
                                                   pdf_layer_absent=_pdf_layer_absent)
                if result is not None:
                    doc.close()
                    return result

        doc.close()
        return None

    # ─────────────────────────────────────────────────────────────────
    # NÚCLEO DE DETECCIÓN  (operando sobre ROI ya recortado)
    # ─────────────────────────────────────────────────────────────────

    def _detect_srri_in_roi(self, roi: np.ndarray, tag: str = "",
                             pdf_layer_absent: bool = False) -> Optional[int]:
        """
        Intenta detectar el SRRI dentro de un ROI ya recortado cerca del widget.
        pdf_layer_absent=True → [V12-FIX-3] salta el band-scan Tesseract
        (inútil si no hay capa de texto vectorial en el PDF).
        """

        # ── PASO 1: OCR para localizar la franja de la fila 1-7 ──────────
        digit_row = self._ocr_digit_row(roi)

        if digit_row is not None:
            # Tenemos posiciones x de al menos 4-6 dígitos con sus y, h
            # Construir la franja de la fila
            strip = self._extract_widget_strip(roi, digit_row)
            if strip is not None:
                strip_img, strip_y_offset = strip

                if DEBUG:
                    cv2.imwrite(os.path.join(self.debug_dir, f"{tag}_strip.png"), strip_img)

                # ── PASO 2b: score directamente con grid desde OCR ────────
                # [V12-FIX-7] OCR+grid ANTES que cell detection.
                # En KIIDs a dos columnas el strip incluye columna derecha; la
                # detección de celdas fusiona contornos del widget con texto
                # adyacente y asigna SRRI incorrecto (LU0982976267: SRRI=2 vs 4).
                # OCR+grid usa posiciones exactas de dígitos → extrapolación fiel.
                winner = self._score_with_ocr_grid(roi, digit_row)
                if winner is not None:
                    if DEBUG:
                        print(f"[V6] [{tag}] RESULT via OCR+grid: {winner}")
                    return winner

                # ── PASO 2a: detectar 7 celdas en la franja (fallback) ────
                cells = self._find_cells_in_strip(strip_img)

                if cells and len(cells) >= 5:
                    winner = self._score_cells_v6(strip_img, cells)
                    if winner is not None:
                        if DEBUG:
                            self._draw_cells(strip_img, cells, winner, f"{tag}_cells")
                            print(f"[V6] [{tag}] RESULT via OCR+cells: {winner}")
                        return winner

        # ── PASO 2c-PRE: No-OCR como pre-check antes del band-scan ──────────
        # [V14-FIX-1 / FIX-NOOCR-FIRST] Para widgets raster en PDFs con capa
        # de texto (e.g. BlackRock IE00B45H7020, Groupama FR*):
        #   • pdf_layer_absent=False → el band-scan NO se salta (PASO 2c)
        #   • Pero el widget es raster: las ~17-26 llamadas Tesseract del
        #     band-scan fallan (~12-18s de OCR inútil).
        #   • _detect_widget_no_ocr (puro OpenCV, sin Tesseract) resuelve el
        #     widget en 2 passes con early-exit en <0.1s.
        #   Fix: intentar no-OCR ANTES del band-scan. Si retorna resultado,
        #   retornar inmediatamente. Si retorna None (incertidumbre),
        #   continuar con band-scan como fallback sin ningún cambio.
        #   Impacto medido: BlackRock 22s→~2s, Groupama 14s→~2s.
        #   Cero regresiones: fondos que necesitan band-scan (widget vectorial,
        #   OCR correcto) llegan aquí con no-OCR=None y band-scan inalterado.
        _pre_winner = self._detect_widget_no_ocr(roi, tag)
        if _pre_winner is not None:
            if DEBUG:
                print(f"[V14] [{tag}] RESULT via no-OCR pre-check: {_pre_winner}")
            return _pre_winner

        # ── PASO 2c: Band-scan OCR con 2x upscale + PSM11 ─────────────────
        # [V12-FIX-3] Si pdf_layer_absent=True, el widget es raster. El OCR
        # (band-scan y preprocesado) no encontrará dígitos → saltar al no-OCR.
        if not pdf_layer_absent:
            # [V10-FIX-1] Para widgets donde PSM6 en ROI grande falla,
            # escanear bandas estrechas con 2x upscale y PSM11.
            _h_roi, _w_roi = roi.shape[:2]
            _gray_roi_bs = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _BAND_H, _BAND_STEP = 200, 50
            _PSM11 = "--psm 11 -c tessedit_char_whitelist=1234567"
            # [V16-FIX-1] MAX_BAND_ITERATIONS: limitar el band-scan para PDFs
            # donde el cluster PDF fue rechazado (layout 2 columnas, EdR, etc.).
            # Sin límite, 2-column PDFs ejecutan 25-37 iteraciones Tesseract sin éxito.
            # Con límite: máximo 15 iteraciones = ~10s en lugar de ~26s por ROI.
            # El no-OCR pre-check (FIX-NOOCR-FIRST) ya ha corrido antes;
            # si no resolvió, el band-scan tiene baja probabilidad de éxito en estos PDFs.
            _MAX_BAND_ITER = 15
            _band_iter_count = 0
            for _bs_y0 in range(0, max(1, _h_roi - _BAND_H + 1), _BAND_STEP):
                _band_iter_count += 1
                if _band_iter_count > _MAX_BAND_ITER:
                    if DEBUG:
                        print(f"[V16] band-scan abortado tras {_MAX_BAND_ITER} iter → no-OCR fallback")
                    break
                _bs_y1 = min(_h_roi, _bs_y0 + _BAND_H)
                _band = _gray_roi_bs[_bs_y0:_bs_y1, :]
                _band_up = cv2.resize(_band, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
                _data_b = pytesseract.image_to_data(_band_up, config=_PSM11,
                                                     output_type=pytesseract.Output.DICT)
                _digs_b = []
                for _bi, _bt in enumerate(_data_b["text"]):
                    if _bt.strip() in "1234567" and len(_bt.strip()) == 1 and _data_b["height"][_bi] > 5:
                        _digs_b.append({
                            "val": int(_bt),
                            "x": int(_data_b["left"][_bi] / 2),
                            "y": int(_data_b["top"][_bi] / 2) + _bs_y0,
                            "w": max(1, int(_data_b["width"][_bi] / 2)),
                            "h": max(1, int(_data_b["height"][_bi] / 2)),
                            "x_center": (_data_b["left"][_bi] + _data_b["width"][_bi] / 2.0) / 2.0,
                        })
                if len(set(d["val"] for d in _digs_b)) < 4:
                    continue
                _by_val_b: Dict[int, list] = {}
                for _d in _digs_b:
                    _by_val_b.setdefault(_d["val"], []).append(_d)
                _dedup_b = sorted(
                    [min(_v, key=lambda _e: abs(_e["x_center"] -
                         float(np.median([_x["x_center"] for _x in _v]))))
                     for _v in _by_val_b.values()],
                    key=lambda _d: _d["x_center"]
                )
                _vals_ord = [_d["val"] for _d in _dedup_b]
                if _vals_ord != sorted(_vals_ord) or len(_vals_ord) < 4:
                    continue
                _xs_b = [_d["x_center"] for _d in _dedup_b]
                _gaps_b = np.diff(_xs_b)
                if len(_gaps_b) == 0 or float(np.mean(_gaps_b)) < 1:
                    continue
                _cv_b = float(np.std(_gaps_b) / (np.mean(_gaps_b) + 1e-9))
                if _cv_b > 0.3:
                    continue
                _winner_b = self._score_with_ocr_grid(roi, _dedup_b)
                if _winner_b is not None:
                    if DEBUG:
                        print(f"[V12] [{tag}] RESULT via band-OCR "
                              f"(y={_bs_y0}-{_bs_y1}, vals={_vals_ord}): {_winner_b}")
                    return _winner_b
            # fin band-scan

            # ── PASO 3: Retry con preprocesado antes del fallback no-OCR ────
            # [V8-FIX-C] Cuando OCR estándar falla, intentar con CLAHE e inversión.
            gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            clahe_op = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            for prep_gray, prep_label in [
                (clahe_op.apply(gray_roi), "clahe"),
                (cv2.bitwise_not(gray_roi), "inv"),
            ]:
                prep_bgr = cv2.cvtColor(prep_gray, cv2.COLOR_GRAY2BGR)
                digit_row_prep = self._ocr_digit_row(prep_bgr)
                if digit_row_prep is not None:
                    winner = self._score_with_ocr_grid(roi, digit_row_prep)
                    if winner is not None:
                        if DEBUG:
                            print(f"[V8] [{tag}] RESULT via OCR-{prep_label}+grid: {winner}")
                        return winner
                    strip = self._extract_widget_strip(roi, digit_row_prep)
                    if strip is not None:
                        strip_img, _ = strip
                        cells2 = self._find_cells_in_strip(strip_img)
                        if cells2 and len(cells2) >= 5:
                            winner = self._score_cells_v6(strip_img, cells2)
                            if winner is not None:
                                if DEBUG:
                                    print(f"[V8] [{tag}] RESULT via OCR-{prep_label}+cells: {winner}")
                                return winner

        # ── PASO 4: Sin OCR – búsqueda directa de widget en ROI ──────────
        winner = self._detect_widget_no_ocr(roi, tag)
        if winner is not None:
            if DEBUG:
                print(f"[V6] [{tag}] RESULT via no-OCR widget search: {winner}")
            return winner

        return None

    # ─────────────────────────────────────────────────────────────────
    # OCR: detectar fila de dígitos 1-7
    # ─────────────────────────────────────────────────────────────────

    def _ocr_digit_row(self, image: np.ndarray) -> Optional[List[dict]]:
        """
        Devuelve la lista de dígitos {val, x_center, y, h, w} de la fila 1-7,
        o None si no hay cluster suficiente.
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Pre-scan: necesitamos al menos algunos contornos plausibles
        _, th = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        h_img, w_img = gray.shape
        plausible = 0
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if h_img * 0.005 < h < h_img * 0.20 and 3 < w < w_img * 0.15:
                asp = h / float(w) if w > 0 else 0
                if 0.8 < asp < 8.0:
                    plausible += 1
        if plausible < 3:
            return None

        # OCR
        data = pytesseract.image_to_data(gray, config=TESS_CONFIG,
                                          output_type=pytesseract.Output.DICT)
        digits = []
        for i, txt in enumerate(data["text"]):
            if txt.strip() in "1234567" and len(txt.strip()) == 1:
                x = data["left"][i]; y = data["top"][i]
                w = data["width"][i]; h = data["height"][i]
                if h > 0 and w > 0:
                    digits.append({"val": int(txt), "x": x, "y": y,
                                   "w": w, "h": h,
                                   "x_center": x + w / 2.0})

        if len(digits) < 4:
            return None

        # Clustering por Y
        med_h = float(np.median([d["h"] for d in digits]))
        tol = med_h * 1.8
        clusters: List[List[dict]] = []
        for d in sorted(digits, key=lambda x: x["y"]):
            placed = False
            for cl in clusters:
                cy = float(np.median([c["y"] for c in cl]))
                if abs(d["y"] - cy) < tol:
                    cl.append(d)
                    placed = True
                    break
            if not placed:
                clusters.append([d])

        # Mejor cluster = más valores únicos 1-7
        best = max(clusters, key=lambda cl: len(set(d["val"] for d in cl)), default=None)
        if best is None:
            return None

        unique_vals = set(d["val"] for d in best)
        if len(unique_vals) < 4:
            return None

        # Deduplicar por val (mantener el más representativo)
        by_val: Dict[int, List[dict]] = {}
        for d in best:
            by_val.setdefault(d["val"], []).append(d)
        deduped = []
        for val, entries in by_val.items():
            if len(entries) == 1:
                deduped.append(entries[0])
            else:
                med_x = float(np.median([e["x_center"] for e in entries]))
                deduped.append(min(entries, key=lambda e: abs(e["x_center"] - med_x)))

        # Filtrar por altura coherente
        med_h2 = float(np.median([d["h"] for d in deduped]))
        deduped = [d for d in deduped if abs(d["h"] - med_h2) < med_h2 * 0.7]

        if len(set(d["val"] for d in deduped)) < 4:
            return None

        if DEBUG:
            for d in sorted(deduped, key=lambda x: x["x_center"]):
                print(f"  OCR digit: {d}")

        return deduped

    # ─────────────────────────────────────────────────────────────────
    # Extraer franja horizontal del widget
    # ─────────────────────────────────────────────────────────────────

    def _extract_widget_strip(self, image: np.ndarray,
                               digit_row: List[dict]) -> Optional[Tuple[np.ndarray, int]]:
        """
        [V7-FIX-2] Amplía el padding izquierdo para capturar la celda 1 cuando
        el primer dígito detectado por OCR es el 2 (celda 1 coloreada, texto
        blanco no detectado). El padding izquierdo es ahora 4×cell_w_est.
        """
        h_img, w_img = image.shape[:2]

        row_y = float(np.median([d["y"] for d in digit_row]))
        row_h = float(np.median([d["h"] for d in digit_row]))

        # Margen vertical generoso para capturar el fondo de la celda
        pad_v = int(row_h * 1.5)
        y1 = int(max(0, row_y - pad_v))
        y2 = int(min(h_img, row_y + row_h + pad_v))

        # Estimar anchura de celda por el gap entre dígitos OCR adyacentes
        sorted_by_val = sorted(digit_row, key=lambda d: d["val"])
        if len(sorted_by_val) >= 2:
            gaps = []
            for i in range(len(sorted_by_val) - 1):
                dv = sorted_by_val[i+1]["val"] - sorted_by_val[i]["val"]
                dx = sorted_by_val[i+1]["x_center"] - sorted_by_val[i]["x_center"]
                if dv > 0:
                    gaps.append(dx / dv)
            cell_w_est = float(np.median(gaps)) if gaps else float(np.median([d["w"] for d in digit_row])) * 3
        else:
            cell_w_est = float(np.median([d["w"] for d in digit_row])) * 3

        xs = sorted([d["x"] for d in digit_row])
        xe = sorted([d["x"] + d["w"] for d in digit_row])
        min_val = min(d["val"] for d in digit_row)

        # [V7-FIX-2] Si el mínimo detectado es 2 (celda 1 no leída),
        # añadir espacio extra a la izquierda equivalente a una celda completa
        extra_left = cell_w_est if min_val > 1 else 0
        x1 = int(max(0, xs[0] - cell_w_est - extra_left))
        x2 = int(min(w_img, xe[-1] + cell_w_est))

        strip = image[y1:y2, x1:x2]
        if strip.size == 0:
            return None

        return strip, y1

    # ─────────────────────────────────────────────────────────────────
    # Detectar 7 celdas dentro de la franja del widget
    # ─────────────────────────────────────────────────────────────────

    def _find_cells_in_strip(self, strip: np.ndarray) -> Optional[List[dict]]:
        """
        Dentro de la franja horizontal del widget, detecta las 7 celdas
        por análisis de contornos rectangulares.
        Devuelve lista ordenada por x, o None.
        """
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        h_s, w_s = gray.shape

        # Threshold adaptativo para detectar bordes de celdas
        th_adapt = cv2.adaptiveThreshold(gray, 255,
                                          cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY_INV,
                                          blockSize=11, C=3)
        # También threshold fijo para fondos claros
        _, th_fixed = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)
        th = cv2.bitwise_or(th_adapt, th_fixed)

        # Morfología: cerrar gaps en los bordes de celdas
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=1)

        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidates = []
        for cnt in cnts:
            x, y, w, h = cv2.boundingRect(cnt)
            # La celda debe ser más ancha que alta (widget horizontal)
            if w < h_s * 0.5:
                continue
            # Altura: entre 30% y 95% de la franja
            if h < h_s * 0.25 or h > h_s * 0.98:
                continue
            # Ancho razonable para una celda
            if w < w_s * 0.03 or w > w_s * 0.50:
                continue
            candidates.append({"x": x, "y": y, "w": w, "h": h,
                                "cx": x + w // 2, "cy": y + h // 2})

        if not candidates:
            return None

        # Agrupar por fila (cy similar)
        candidates.sort(key=lambda c: c["cy"])
        rows = self._group_by_row(candidates, rel_tol=0.6)

        best_row = None
        best_score = 0
        for row in rows:
            if len(row) < 5:
                continue
            row_sorted = sorted(row, key=lambda c: c["cx"])
            # Verificar equidistancia
            xs = [c["cx"] for c in row_sorted]
            if len(xs) < 2:
                continue
            gaps = np.diff(xs)
            cv = float(np.std(gaps) / (np.mean(gaps) + 1e-9))
            score = len(row_sorted) - cv * 3
            if score > best_score:
                best_score = score
                best_row = row_sorted

        if best_row is None:
            return None

        # Seleccionar exactamente 7
        if len(best_row) > 7:
            best_row = self._pick_equidistant_7(best_row)
        if len(best_row) < 5:
            return None

        return best_row

    def _group_by_row(self, cells: List[dict], rel_tol: float = 0.6) -> List[List[dict]]:
        rows: List[List[dict]] = []
        for cell in cells:
            placed = False
            for row in rows:
                row_cy = float(np.median([c["cy"] for c in row]))
                row_h = float(np.median([c["h"] for c in row]))
                if abs(cell["cy"] - row_cy) < row_h * rel_tol:
                    row.append(cell)
                    placed = True
                    break
            if not placed:
                rows.append([cell])
        return rows

    def _pick_equidistant_7(self, cells_sorted: List[dict]) -> List[dict]:
        """De una lista ordenada por x, elige los 7 con gaps más uniformes."""
        best = cells_sorted[:7]
        best_cv = float("inf")
        n = len(cells_sorted)
        for start in range(n - 6):
            subset = cells_sorted[start:start + 7]
            xs = [c["cx"] for c in subset]
            gaps = np.diff(xs)
            cv = float(np.std(gaps) / (np.mean(gaps) + 1e-9))
            if cv < best_cv:
                best_cv = cv
                best = subset
        return best

    # ─────────────────────────────────────────────────────────────────
    # SCORER V6: mide color en cada celda detectada
    # ─────────────────────────────────────────────────────────────────

    def _score_cells_v6(self, image: np.ndarray,
                         cells: List[dict]) -> Optional[int]:
        """
        Dado un conjunto de celdas (máx 7) ordenadas por cx (izquierda→derecha),
        determina cuál tiene color diferente al resto.
        Devuelve el número de SRRI (1-7) o None.
        """
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        h_img, w_img = image.shape[:2]

        n = len(cells)
        sats, vals_v, means_b = [], [], []

        for cell in cells:
            x1 = max(0, cell["x"])
            y1 = max(0, cell["y"])
            x2 = min(w_img, cell["x"] + cell["w"])
            y2 = min(h_img, cell["y"] + cell["h"])

            # Margen del 15% para evitar bordes
            mx = max(1, (x2 - x1) // 7)
            my = max(1, (y2 - y1) // 7)
            roi_hsv = hsv[y1 + my:y2 - my, x1 + mx:x2 - mx]
            roi_bgr = image[y1 + my:y2 - my, x1 + mx:x2 - mx]

            if roi_hsv.size == 0:
                sats.append(0.0)
                vals_v.append(255.0)
                means_b.append(255.0)
                continue

            sats.append(float(np.mean(roi_hsv[:, :, 1])))
            vals_v.append(float(np.mean(roi_hsv[:, :, 2])))
            means_b.append(float(np.mean(roi_bgr)))  # media B+G+R combinada

        if DEBUG:
            print(f"  cell sats: {[round(s,1) for s in sats]}")
            print(f"  cell vals: {[round(v,1) for v in vals_v]}")

        darks = [255.0 - v for v in vals_v]  # alto = celda oscura

        winner_idx = self._best_outlier(sats, darks)

        # [V8-FIX-B] Cuando el fondo del widget ya está saturado (p.ej. azul),
        # la saturación media de TODAS las celdas es alta y uniforme, haciendo
        # que _best_outlier devuelva None. En ese caso, intentamos detectar
        # el outlier por DIVERGENCIA DE HUE: la celda coloreada tendrá un hue
        # distinto al resto del fondo (p.ej. amarillo vs azul).
        if winner_idx is None and len(sats) >= 5:
            sat_std = float(np.std(sats))
            sat_mean = float(np.mean(sats))
            # Si la saturación media es alta (fondo coloreado) y casi uniforme
            if sat_mean > 80 and sat_std < 8.0:
                hues = []
                for cell in cells:
                    x1 = max(0, cell["x"])
                    y1 = max(0, cell["y"])
                    x2 = min(w_img, cell["x"] + cell["w"])
                    y2 = min(h_img, cell["y"] + cell["h"])
                    mx = max(1, (x2 - x1) // 7)
                    my = max(1, (y2 - y1) // 7)
                    roi_h = hsv[y1 + my:y2 - my, x1 + mx:x2 - mx, 0]  # canal Hue
                    if roi_h.size == 0:
                        hues.append(0.0)
                        continue
                    # Circular mean del hue (en grados 0-180 en OpenCV)
                    h_rad = roi_h.astype(float) * (np.pi / 90.0)
                    sin_m = float(np.mean(np.sin(h_rad)))
                    cos_m = float(np.mean(np.cos(h_rad)))
                    hue_mean = float(np.arctan2(sin_m, cos_m) * 90.0 / np.pi) % 180.0
                    hues.append(hue_mean)
                if DEBUG:
                    print(f"  [V8-FIX-B] fondo saturado (sat_mean={sat_mean:.1f} std={sat_std:.1f}), probando hue")
                    print(f"  cell hues: {[round(h,1) for h in hues]}")
                # Medir divergencia de hue: circular distance de cada celda al hue mediano
                hue_arr = np.array(hues)
                med_hue = float(np.median(hue_arr))
                diffs = []
                for h in hues:
                    d = abs(h - med_hue)
                    d = min(d, 180.0 - d)  # distancia circular
                    diffs.append(d)
                winner_idx = self._best_outlier(diffs, [0.0] * len(diffs))
                if DEBUG and winner_idx is not None:
                    print(f"  [V8-FIX-B] hue winner_idx={winner_idx} (hue_diff={diffs[winner_idx]:.1f})")

        # [V10-FIX-2] Si winner_idx es None con n<7 celdas, la celda resaltada
        # puede estar FUERA del rango detectado. Extrapolamos a izquierda/derecha.
        # Caso real: IE00B45H7020 tiene celdas 2-7 negras detectadas (n=6) y
        # celda 1 naranja no detectada. El color extrapolado a la izquierda
        # (sat=233) es muy diferente a la media de las detectadas (sat≈0).
        # SRRI: outlier izquierdo → siempre 1; outlier derecho → siempre 7.
        if winner_idx is None and n >= 5 and n < 7:
            _cells_ext = sorted(cells, key=lambda c: c["cx"])
            _gap_ext = float(np.median(np.diff([c["cx"] for c in _cells_ext])))
            if _gap_ext > 0:
                _y0_ext = int(np.min([c["y"] for c in _cells_ext]))
                _y1_ext = int(np.max([c["y"] + c["h"] for c in _cells_ext]))
                _wcell = max(4, int(_gap_ext * 0.85))
                _cx_left  = _cells_ext[0]["cx"]  - _gap_ext
                _cx_right = _cells_ext[-1]["cx"] + _gap_ext
                _mean_sat_det = float(np.mean(sats))
                _mean_drk_det = float(np.mean(darks))
                _best_extra: Optional[tuple] = None  # (srri, score)
                for _cx_e, _label, _srri_e in [
                    (_cx_left,  "left",  1),
                    (_cx_right, "right", 7),
                ]:
                    _x1e = max(0, int(_cx_e - _wcell // 2))
                    _x2e = min(w_img, int(_cx_e + _wcell // 2))
                    if _x1e >= _x2e:
                        continue
                    _roi_e = hsv[_y0_ext:_y1_ext, _x1e:_x2e]
                    if _roi_e.size == 0:
                        continue
                    _sat_e  = float(np.mean(_roi_e[:, :, 1]))
                    _drk_e  = 255.0 - float(np.mean(_roi_e[:, :, 2]))
                    _diff_e = abs(_sat_e - _mean_sat_det) + abs(_drk_e - _mean_drk_det)
                    if DEBUG:
                        print(f"  [V10-FIX-2] extrap {_label} cx={_cx_e:.0f}: "
                              f"sat={_sat_e:.1f} dark={_drk_e:.1f} diff={_diff_e:.1f}")
                    # Umbral: la celda extrapolada debe ser MUY diferente
                    if (abs(_sat_e - _mean_sat_det) > 30 or
                            abs(_drk_e - _mean_drk_det) > 50):
                        if _best_extra is None or _diff_e > _best_extra[1]:
                            _best_extra = (_srri_e, _diff_e)
                if _best_extra is not None:
                    if DEBUG:
                        print(f"  [V10-FIX-2] winner SRRI={_best_extra[0]}")
                    return _best_extra[0]

        if winner_idx is None:
            return None

        # El índice 0 = celda más a la izquierda = dígito 1
        # → SRRI = winner_idx + 1  (si hay 7 celdas, posición 0→1, 1→2…)
        # Pero si hay < 7 celdas, necesitamos inferir el dígito real.
        # Usamos el número de orden de izquierda a derecha.
        if n == 7:
            srri = winner_idx + 1
        else:
            # Estimar dígito interpolando entre las celdas disponibles
            # Asumir que el gap es uniforme y que van de 1 a 7
            srri = self._estimate_digit(cells, winner_idx)

        if 1 <= srri <= 7:
            return srri
        return None

    def _best_outlier(self, sats: List[float], darks: List[float]) -> Optional[int]:
        """
        [V7-FIX-3] Devuelve el índice de la celda que más difiere del resto.
        Mejora: cuando hay exactamente dos valores consecutivos altos
        (celda coloreada + borde capturado por el ROI adyacente), devuelve
        el de mayor z en lugar de rechazar ambos por baja dominancia.
        """
        def zscore_winner(scores):
            arr = np.array(scores, dtype=float)
            mn, sd = np.mean(arr), np.std(arr)
            if sd < 0.5:
                return None, -1.0
            z = (arr - mn) / sd
            idx = int(np.argmax(z))
            mz = float(z[idx])
            sorted_z = sorted(z, reverse=True)
            dom = sorted_z[0] - sorted_z[1] if len(sorted_z) > 1 else 0.0

            # Umbral normal
            if mz > 1.2 and dom > 0.20:
                return idx, mz

            # [V7-FIX-3] Caso de dos valores consecutivos altos: acepta si
            # el segundo candidato está adyacente al primero y mz es claro.
            # En este caso, devuelve el ÍNDICE MÁS BAJO (celda real, no el
            # bleed en la celda adyacente derecha causado por el ROI del grid).
            if mz > 1.5 and dom < 0.20:
                # Buscar si hay exactamente 2 outliers (z > 1.0)
                high_idx = [i for i, zv in enumerate(z) if zv > 1.0]
                if len(high_idx) == 2 and abs(high_idx[0] - high_idx[1]) == 1:
                    # El índice más bajo es el centro de la celda real;
                    # el índice más alto captura el borde derecho por bleed del ROI
                    return int(min(high_idx)), float(z[min(high_idx)])

            return None, -1.0

        idx_s, z_s = zscore_winner(sats)
        idx_d, z_d = zscore_winner(darks)

        if DEBUG:
            arr_s = np.array(sats); mn_s = np.mean(arr_s); sd_s = np.std(arr_s)
            arr_d = np.array(darks); mn_d = np.mean(arr_d); sd_d = np.std(arr_d)
            zs = [(s-mn_s)/sd_s if sd_s>0 else 0 for s in sats]
            zd = [(d-mn_d)/sd_d if sd_d>0 else 0 for d in darks]
            print(f"  z_sat={[round(z,2) for z in zs]}  winner_sat={idx_s}(z={z_s:.2f})")
            print(f"  z_dark={[round(z,2) for z in zd]}  winner_dark={idx_d}(z={z_d:.2f})")

        if idx_s is not None and idx_d is not None:
            return idx_s if z_s >= z_d else idx_d
        if idx_s is not None:
            return idx_s
        if idx_d is not None:
            return idx_d
        return None

    def _estimate_digit(self, cells: List[dict], winner_idx: int) -> int:
        """
        Estima el dígito SRRI cuando hay < 7 celdas detectadas.
        Usa la posición x del winner relativa al ancho total del widget.
        NOTA: tiene error ±1 cuando faltan celdas por la izquierda.
        Preferir _score_cells_grid_aligned cuando se dispone del ROI completo.
        """
        if not cells:
            return winner_idx + 1
        x_min = min(c["cx"] for c in cells)
        x_max = max(c["cx"] for c in cells)
        span = x_max - x_min
        if span <= 0:
            return winner_idx + 1
        winner_x = cells[winner_idx]["cx"]
        frac = (winner_x - x_min) / span
        return max(1, min(7, round(1 + frac * 6)))

    def _score_cells_grid_aligned(self, image: np.ndarray,
                                   cells: List[dict]) -> Optional[int]:
        """
        [V12-FIX-5] Scoring con alineación de grid para n < 7 celdas detectadas.

        Problema de _estimate_digit: cuando faltan celdas por la IZQUIERDA
        (p.ej. celda 1 oscura no detectada), asume que la primera celda detectada
        es la posición 1 → error sistemático de -1 en el SRRI.

        Ejemplo BlackRock LU0171275786 (6 celdas = widget 2-7):
          _estimate_digit → SRRI=3 (incorrecto), este método → SRRI=4 ✓

        Solución: probar todos los desplazamientos k=0..(7-n) que mapean las
        n celdas detectadas a las posiciones widget (k+1)..(k+n). Para cada k,
        medir color SAT/DARK en las 7 posiciones extrapoladas y usar z-score.
        Tiebreaker: preferir la alineación donde TODAS las 7 posiciones caen
        dentro de la imagen (in_bounds=7 > 6 da score más alto).
        """
        if not cells:
            return None
        n = len(cells)
        if n == 7:
            return self._score_cells_v6(image, cells)
        if n < 4:
            return None

        h_img, w_img = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

        row_sorted = sorted(cells, key=lambda c: c["cx"])
        xs = [float(c["cx"]) for c in row_sorted]
        gap = (xs[-1] - xs[0]) / (n - 1)
        if gap < 5:
            return None

        cell_w = int(gap * 0.85)
        half = cell_w // 2
        row_y = float(np.median([c["cy"] for c in row_sorted]))
        row_h = float(np.median([c["h"] for c in row_sorted]))
        y0 = int(max(0, row_y - row_h))
        y1 = int(min(h_img, row_y + row_h))

        def _z_winner_quiet(scores):
            arr = np.array(scores, dtype=float)
            mn, sd = np.mean(arr), np.std(arr)
            if sd < 0.5:
                return None, -1.0
            z = (arr - mn) / sd
            idx = int(np.argmax(z))
            mz = float(z[idx])
            sorted_z = sorted(z, reverse=True)
            dom = sorted_z[0] - sorted_z[1] if len(sorted_z) > 1 else 0.0
            if mz > 1.2 and dom > 0.20:
                return idx, mz
            if mz > 1.5 and dom < 0.20:
                high = [i for i, zv in enumerate(z) if zv > 1.0]
                if len(high) == 2 and abs(high[0] - high[1]) == 1:
                    return int(min(high)), float(z[min(high)])
            return None, -1.0

        best_srri: Optional[int] = None
        best_score = -999.0

        for k in range(7 - n + 1):
            x_cell1 = xs[0] - k * gap
            all7_cx = [x_cell1 + i * gap for i in range(7)]
            in_bounds = sum(1 for cx in all7_cx if 0 <= cx < w_img)
            if in_bounds < n:
                continue

            sats, darks = [], []
            for cx in all7_cx:
                x0 = max(0, int(cx - half))
                x2 = min(w_img, int(cx + half))
                if x0 >= x2:
                    sats.append(0.0); darks.append(0.0)
                    continue
                roi_cell = hsv[y0:y1, x0:x2]
                sats.append(float(np.mean(roi_cell[:, :, 1])))
                darks.append(255.0 - float(np.mean(roi_cell[:, :, 2])))

            idx_s, z_s = _z_winner_quiet(sats)
            idx_d, z_d = _z_winner_quiet(darks)

            if idx_s is not None and idx_d is not None:
                winner_idx = idx_s if z_s >= z_d else idx_d
                z_win = max(z_s, z_d)
            elif idx_s is not None:
                winner_idx, z_win = idx_s, z_s
            elif idx_d is not None:
                winner_idx, z_win = idx_d, z_d
            else:
                continue

            srri = winner_idx + 1
            if not (1 <= srri <= 7):
                continue

            score = in_bounds * 10.0 + z_win
            if DEBUG:
                print(f"  [grid-align] k={k} (celdas {k+1}..{k+n}): "
                      f"winner=idx{winner_idx}(z={z_win:.2f}) SRRI={srri} "
                      f"in_bounds={in_bounds}")
            if score > best_score:
                best_score = score
                best_srri = srri

        return best_srri

    # ─────────────────────────────────────────────────────────────────
    # SCORING DIRECTO CON GRID OCR (cuando no se detectan celdas)
    # ─────────────────────────────────────────────────────────────────


    def _score_with_ocr_grid(self, image: np.ndarray,
                              digit_row: List[dict]) -> Optional[int]:
        """
        Construye un grid algebraico desde los dígitos OCR y mide color
        en cada posición interpolada/extrapolada.
        Versión mejorada: el ROI de cada celda se determina por los gaps
        entre los dígitos OCR adyacentes, no por half_w fijo.
        """
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        h_img, w_img = image.shape[:2]

        row = sorted(digit_row, key=lambda d: d["val"])
        vals = [d["val"] for d in row]
        xs = np.array([d["x_center"] for d in row])

        if len(set(vals)) < 2:
            return None

        # [V12-FIX-8] Rechazar outliers antes del polyfit.
        # Problema: Tesseract (especialmente en Windows) puede devolver falsos
        # positivos para un dígito que ya estaba en el cluster (p.ej. val=3 a
        # y=59 w=1106 desde un bloque de texto) mientras que otro dígito del
        # widget tiene el mismo val a la posición correcta. Cuando el falso
        # positivo se cuela en digit_row (por tol laxa en clustering), el
        # polyfit se distorsiona y val=correcto mapea a la posición del falso
        # positivo → SRRI incorrecto.
        # 
        # Solución RANSAC-lite: si n_digits > 3, hacer polyfit con los puntos
        # disponibles, calcular residuales, descartar outliers con residual >
        # 1.5 × cell_width estimada, y repetir el fit con los inliers.
        # Aplicar solo si quedan ≥ 3 inliers (para preservar casos con pocos dígitos).
        if len(vals) >= 4:
            m_init, c_init = np.polyfit(vals, xs, 1)
            if m_init > 0:
                cell_w_est = abs(m_init) * 0.85
                threshold = max(cell_w_est * 2.0, 30.0)
                residuals = np.abs(xs - (m_init * np.array(vals) + c_init))
                inlier_mask = residuals < threshold
                if inlier_mask.sum() >= 3 and inlier_mask.sum() < len(vals):
                    outliers = [(vals[i], xs[i], residuals[i]) for i in range(len(vals)) if not inlier_mask[i]]
                    if DEBUG:
                        for v, x_out, r in outliers:
                            print(f"  [V12-FIX-8] outlier rechazado: val={v} cx={x_out:.0f} residual={r:.0f} > {threshold:.0f}")
                    row  = [d for d, ok in zip(row, inlier_mask) if ok]
                    vals = [d["val"] for d in row]
                    xs   = np.array([d["x_center"] for d in row])

        if len(set(vals)) < 2:
            return None

        # Polyfit: xs = m*val + c
        m, c = np.polyfit(vals, xs, 1)
        if m <= 0:
            return None

        # [V7-FIX-3] cell_w reducido a 0.38 para medir interior de celda,
        # evitando capturar parte de la celda adyacente (que diluía la señal
        # en casos como LU0210536867 donde la celda coloreada tiene borde compartido)
        cell_w = abs(m) * 0.85  # 85% del gap entre centros ≈ ancho interior

        # Altura de la franja - [V8-FIX-C] ampliar para capturar celda completa.
        # Cuando la celda 1 es de color (texto blanco, dígito no leído por OCR),
        # el strip basado en row_y/row_h de los dígitos 2-7 puede ser demasiado
        # estrecho y no cubrir bien la zona coloreada.
        row_y = float(np.median([d["y"] for d in digit_row]))
        row_h = float(np.median([d["h"] for d in digit_row]))
        # Ampliar: 2× arriba y 2.5× abajo del centro para cubrir toda la celda
        y1 = int(max(0, row_y - row_h * 2.0))
        y2 = int(min(h_img, row_y + row_h * 2.5))

        sats, darks = [], []

        for digit_val in range(1, 8):
            cx = m * digit_val + c
            x1 = int(max(0, cx - cell_w * 0.38))
            x2 = int(min(w_img, cx + cell_w * 0.38))

            roi = hsv[y1:y2, x1:x2]
            if roi.size == 0:
                sats.append(0.0)
                darks.append(0.0)
                continue

            sats.append(float(np.mean(roi[:, :, 1])))
            darks.append(255.0 - float(np.mean(roi[:, :, 2])))

        if DEBUG:
            print(f"  grid sats: {[round(s,1) for s in sats]}")
            print(f"  grid darks: {[round(d,1) for d in darks]}")

        winner_idx = self._best_outlier(sats, darks)
        if winner_idx is None:
            return None

        srri = winner_idx + 1  # grid siempre tiene 7 posiciones (1..7)
        return srri if 1 <= srri <= 7 else None

    # ─────────────────────────────────────────────────────────────────
    # DETECCIÓN SIN OCR: buscar widget directamente en ROI
    # ─────────────────────────────────────────────────────────────────

    def _detect_widget_no_ocr(self, roi: np.ndarray, tag: str = "") -> Optional[int]:
        """
        [V7-FIX-4] Búsqueda directa del widget cuando OCR falla.
        [V12-FIX-2] No-morph pases en PRIMERA posición + early exit.
          • No-morph (sin morphologyEx) es el método que funciona para widgets
            PRIIP-v3 modernos (BlackRock, Amundi, IE00 con celdas pequeñas).
            Antes estaba en posición 13-14 de 14; ahora ocupa las posiciones 1-2.
          • Early exit: en cuanto results acumula 2 votos iguales → return.
            Amundi: era 10 passes → ahora 2 (no-morph thresh=128 confirma ×2).
            BlackRock: era 14 passes → ahora 2.
        """
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        h_r, w_r = gray.shape

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray_clahe = clahe.apply(gray)
        gray_inv = cv2.bitwise_not(gray)

        results = []

        # [V12-FIX-2] Helper para añadir resultado con early exit
        def _add_result(val: int) -> Optional[int]:
            results.append(val)
            if len(results) >= 2 and len(set(results[-2:])) == 1:
                return val     # 2 resultados consecutivos iguales → salir
            return None

        # ── [V12-FIX-2] PRIMERO: pases sin morphologyEx ──────────────────
        # Son los únicos que funcionan para widgets PRIIP-v3 (celdas oscuras
        # sin relleno de color: la morphologyEx CLOSE fusiona celdas adyacentes).
        for nm_thresh in [128, 160]:
            _, th_nm = cv2.threshold(gray, nm_thresh, 255, cv2.THRESH_BINARY_INV)
            cnts_nm, _ = cv2.findContours(th_nm, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cells_nm = []
            for cnt in cnts_nm:
                x, y, w, h = cv2.boundingRect(cnt)
                if w < h_r * 0.04 or w > h_r * 2.5:
                    continue
                if h < h_r * 0.03 or h > h_r * 0.65:
                    continue
                asp = w / float(h)
                if asp < 0.4 or asp > 10.0:
                    continue
                cells_nm.append({"x": x, "y": y, "w": w, "h": h,
                                  "cx": x + w // 2, "cy": y + h // 2})
            if len(cells_nm) < 5:
                # [FIX-BLOB-1] Widget con 7 celdas fusionadas en un solo blob
                # (e.g. Groupama): sin separadores visibles entre celdas, el
                # threshold detecta <5 contornos pero uno de ellos es el widget
                # completo (aspect ratio ≈ 7). Se busca cualquier blob con
                # asp ∈ [5.0, 9.5], se subdivide en 7 columnas iguales y se puntúa.
                # _score_cells_grid_aligned exige z≥2.0 → rechaza falsos positivos.
                blob_candidates = sorted(
                    [c for c in cells_nm if 5.0 <= c["w"] / float(c["h"]) <= 9.5],
                    key=lambda c: c["w"] / float(c["h"]),
                    reverse=True,
                )
                for blob in blob_candidates:
                    sub_w = blob["w"] // 7
                    sub_cells_b = [
                        {"x": blob["x"] + i * sub_w, "y": blob["y"],
                         "w": sub_w, "h": blob["h"],
                         "cx": blob["x"] + i * sub_w + sub_w // 2,
                         "cy": blob["cy"]}
                        for i in range(7)
                    ]
                    winner_blob = self._score_cells_grid_aligned(roi, sub_cells_b)
                    if winner_blob is not None:
                        if DEBUG:
                            print(f"  [no-ocr] blob-split thresh={nm_thresh}: "
                                  f"SRRI={winner_blob}")
                        early = _add_result(winner_blob)
                        if early is not None:
                            return early

                # [V15-FIX-1 / FIX-BLOB-ROBECO] Borde compuesto de asp alto (>10).
                # Causa: KIIDs Robeco (y similares) dibujan el widget con un borde
                # teal continuo: el borde exterior + los 6 divisores internos son
                # una única región conectada. Su bounding box:
                #   w ≈ 89% del ancho de página, h ≈ 35px → asp ≈ 31.7
                # Este blob NO entra en cells_nm (filtrado por asp>10) ni en
                # blob_candidates (asp>9.5). Fix: escanear cnts_nm directamente
                # (sin el filtro asp<=10) con rango asp ∈ [9.0, 42.0] y mínimo
                # w > 20% del ROI (el widget siempre ocupa al menos 1/5 del ancho).
                # Señal de color confirmada: sat_celda4=232 vs sat_resto≈24 → z=2.45
                # Via V14 pre-check, el SRRI se resuelve ANTES del band-scan → 0
                # llamadas Tesseract adicionales. Tiempo: ~20-30s → ~1-2s.
                wide_blob_candidates = []
                for cnt_wb in cnts_nm:
                    xb, yb, wb, hb = cv2.boundingRect(cnt_wb)
                    # Altura: entre 1.5% y 40% del ROI
                    if hb < h_r * 0.015 or hb > h_r * 0.40:
                        continue
                    # Mínimo 20% del ancho del ROI
                    if wb < w_r * 0.20:
                        continue
                    asp_wb = wb / float(hb) if hb > 0 else 0
                    if 9.0 <= asp_wb <= 42.0:
                        wide_blob_candidates.append(
                            {"x": xb, "y": yb, "w": wb, "h": hb,
                             "cx": xb + wb // 2, "cy": yb + hb // 2}
                        )
                wide_blob_candidates.sort(key=lambda c: c["w"], reverse=True)
                for blob_w in wide_blob_candidates:
                    sub_w = blob_w["w"] // 7
                    if sub_w < 4:
                        continue
                    sub_cells_w = [
                        {"x": blob_w["x"] + i * sub_w, "y": blob_w["y"],
                         "w": sub_w, "h": blob_w["h"],
                         "cx": blob_w["x"] + i * sub_w + sub_w // 2,
                         "cy": blob_w["cy"]}
                        for i in range(7)
                    ]
                    winner_wb = self._score_cells_grid_aligned(roi, sub_cells_w)
                    if winner_wb is not None:
                        if DEBUG:
                            print(f"  [no-ocr] wide-blob thresh={nm_thresh} "
                                  f"asp={blob_w['w']/float(blob_w['h']):.1f}: "
                                  f"SRRI={winner_wb}")
                        early = _add_result(winner_wb)
                        if early is not None:
                            return early
                continue
            rows_nm = self._group_by_row(cells_nm)
            for row_nm in rows_nm:
                if len(row_nm) < 5:
                    continue
                row_nm_s = sorted(row_nm, key=lambda c: c["cx"])
                if len(row_nm_s) > 7:
                    row_nm_s = self._pick_equidistant_7(row_nm_s)
                if len(row_nm_s) < 5:
                    continue
                xs_nm = [c["cx"] for c in row_nm_s]
                gaps_nm = np.diff(xs_nm)
                if len(gaps_nm) == 0:
                    continue
                cv_nm = float(np.std(gaps_nm) / (np.mean(gaps_nm) + 1e-9))
                if cv_nm > 0.45:
                    continue
                winner_nm = self._score_cells_grid_aligned(roi, row_nm_s)
                if winner_nm is not None:
                    if DEBUG:
                        print(f"  [no-ocr] no-morph thresh={nm_thresh}: SRRI={winner_nm}")
                    early = _add_result(winner_nm)
                    if early is not None:
                        return early

        # ── DESPUÉS: variantes con morphologyEx (fallback para widgets más complejos)
        image_variants = [
            (roi, gray, "normal"),
            (cv2.bitwise_not(roi), gray_inv, "inverted"),
            (roi, gray_clahe, "clahe"),
        ]

        for img_variant, gray_variant, vname in image_variants:
            for thresh_val in [230, 200, 160, 128]:
                _, th = cv2.threshold(gray_variant, thresh_val, 255, cv2.THRESH_BINARY_INV)
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
                th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel)
                cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                cells = []
                for cnt in cnts:
                    x, y, w, h = cv2.boundingRect(cnt)
                    if w < h_r * 0.04 or w > h_r * 2.5:
                        continue
                    if h < h_r * 0.03 or h > h_r * 0.65:
                        continue
                    asp = w / float(h)
                    if asp < 0.4 or asp > 10.0:
                        continue
                    cells.append({"x": x, "y": y, "w": w, "h": h,
                                   "cx": x + w // 2, "cy": y + h // 2})

                if len(cells) < 6:
                    continue

                rows = self._group_by_row(cells)
                for row_cells in rows:
                    if len(row_cells) < 5:
                        continue
                    row_sorted = sorted(row_cells, key=lambda c: c["cx"])
                    if len(row_sorted) > 7:
                        row_sorted = self._pick_equidistant_7(row_sorted)
                    if len(row_sorted) < 5:
                        continue

                    xs = [c["cx"] for c in row_sorted]
                    gaps = np.diff(xs)
                    if len(gaps) == 0:
                        continue
                    cv_gaps = float(np.std(gaps) / (np.mean(gaps) + 1e-9))
                    if cv_gaps > 0.45:
                        continue

                    score_img = img_variant if vname == "inverted" else roi
                    winner = self._score_cells_grid_aligned(score_img, row_sorted)
                    if winner is not None:
                        if DEBUG:
                            print(f"  [no-ocr] {vname} thresh={thresh_val}: SRRI={winner}")
                        early = _add_result(winner)
                        if early is not None:
                            return early

        # Intentar OCR con imagen preprocesada (CLAHE + inversión)
        for gray_variant, label in [(gray_clahe, "clahe"), (gray_inv, "inv")]:
            data = pytesseract.image_to_data(gray_variant, config=TESS_CONFIG,
                                              output_type=pytesseract.Output.DICT)
            digits_found = [d for d in data["text"] if d.strip() in "1234567" and len(d.strip()) == 1]
            if len(digits_found) >= 4:
                if DEBUG:
                    print(f"  [no-ocr] OCR {label} found {len(digits_found)} digits, retrying...")
                digit_row = self._ocr_digit_row(
                    cv2.cvtColor(gray_variant, cv2.COLOR_GRAY2BGR)
                )
                if digit_row is not None:
                    winner = self._score_with_ocr_grid(roi, digit_row)
                    if winner is not None:
                        early = _add_result(winner)
                        if early is not None:
                            return early

        if not results:
            return None

        from collections import Counter
        counts = Counter(results)
        top = counts.most_common(1)[0]
        return top[0]

    # ─────────────────────────────────────────────────────────────────
    # [V9] EXTRACCIÓN DE POSICIONES DESDE CAPA DE TEXTO PDF
    # ─────────────────────────────────────────────────────────────────

    def _find_digit_row_from_pdf(self, page: fitz.Page) -> Optional[List[dict]]:
        """
        [V9-FIX-1] Localiza la fila de dígitos 1-7 usando la capa de texto
        vectorial del PDF (page.get_text("rawdict")). Sin OCR: las posiciones
        son exactas independientemente del color del texto o del fondo.

        Devuelve lista de dicts {val, x_center, y, h, w, x} en PÍXELES
        al DPI configurado, o None si no se encuentra una fila válida.
        """
        scale = self.dpi / 72.0

        try:
            raw = page.get_text("rawdict")
        except Exception:
            return None

        # Recopilar todos los caracteres "1"-"7" con su bbox
        candidates: List[dict] = []
        for block in raw.get("blocks", []):
            if block.get("type") != 0:   # solo bloques de texto
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    for char in span.get("chars", []):
                        c = char.get("c", "")
                        if c not in "1234567":
                            continue
                        bbox = char.get("bbox")
                        if not bbox or len(bbox) < 4:
                            continue
                        x0, y0, x1, y1 = bbox
                        h = max(float(y1 - y0), 0.5)
                        w = max(float(x1 - x0), 0.5)
                        candidates.append({
                            "val": int(c),
                            "x":        x0 * scale,
                            "y":        y0 * scale,
                            "h":        h  * scale,
                            "w":        w  * scale,
                            "x_center": (x0 + x1) / 2.0 * scale,
                        })

        if len(candidates) < 4:
            return None

        # ── Clustering por Y (igual que _ocr_digit_row) ───────────────────
        med_h = float(np.median([d["h"] for d in candidates]))
        tol = max(med_h * 2.0, 3.0 * scale)   # mínimo 3pt de tolerancia

        clusters: List[List[dict]] = []
        for d in sorted(candidates, key=lambda x: x["y"]):
            placed = False
            for cl in clusters:
                cy = float(np.median([c["y"] for c in cl]))
                if abs(d["y"] - cy) < tol:
                    cl.append(d)
                    placed = True
                    break
            if not placed:
                clusters.append([d])

        # ── Seleccionar mejor cluster ─────────────────────────────────────
        # Criterio: mayor nº de valores únicos, desempate por menor CV de gaps
        # (el widget SRRI tiene gaps MUY uniformes, el texto normal no)
        best: Optional[List[dict]] = None
        best_score = -1.0

        for cl in clusters:
            unique_vals = set(d["val"] for d in cl)
            if len(unique_vals) < 4:
                continue

            # Deduplicar por val: quedarse con la posición mediana en x
            by_val: Dict[int, List[dict]] = {}
            for d in cl:
                by_val.setdefault(d["val"], []).append(d)
            deduped = []
            for entries in by_val.values():
                if len(entries) == 1:
                    deduped.append(entries[0])
                else:
                    med_x = float(np.median([e["x_center"] for e in entries]))
                    deduped.append(
                        min(entries, key=lambda e: abs(e["x_center"] - med_x))
                    )

            if len(set(d["val"] for d in deduped)) < 4:
                continue

            # Verificar que los valores están en orden de izquierda a derecha
            deduped_sorted = sorted(deduped, key=lambda x: x["x_center"])
            vals_in_order = [d["val"] for d in deduped_sorted]
            # El widget siempre aumenta de izquierda a derecha (1→7)
            if vals_in_order != sorted(vals_in_order):
                continue

            # CV de gaps: cuanto más uniforme, más probable que sea el widget
            xs = [d["x_center"] for d in deduped_sorted]
            gaps = np.diff(xs)
            if len(gaps) == 0:
                continue
            cv = float(np.std(gaps) / (np.mean(gaps) + 1e-9))

            # [V12-FIX-6] Rechazar clusters con gap mínimo ≈ 0.
            # Causa: en KIIDs a dos columnas, los dígitos del widget (que está
            # en la columna izquierda) aparecen en la capa de texto con
            # x_center colapsado — mismo x para varios valores (p.ej. val=1,2,4
            # todos en x=878px). Esto ocurre porque el PDF layout a columnas
            # asigna la misma posición horizontal a múltiples dígitos del widget.
            # Con min_gap≈0, polyfit produce m≈0 → todas las posiciones se
            # extrapolan al mismo x → el scoring da SRRI=1 (primer valor) siempre.
            # Criterio: el gap mínimo debe ser ≥ 0.5× la altura del dígito en pts.
            med_h_pts = float(np.median([d["h"] / scale for d in deduped]))
            min_gap_px = float(gaps.min()) if len(gaps) > 0 else 0.0
            if min_gap_px < 0.5 * med_h_pts * scale:
                if DEBUG:
                    print(f"  [pdf-row] cluster rechazado: min_gap={min_gap_px:.1f}px "
                          f"< 0.5×h={0.5*med_h_pts*scale:.1f}px (CV={cv:.2f})")
                continue

            # [V12-FIX-6b] Rechazar clusters con CV > 0.5 (gaps muy irregulares).
            # El widget SRRI tiene gaps uniformes (<0.15 típico). CV>0.5 indica
            # que los x_centers vienen de texto de tabla/escenarios, no del widget.
            if cv > 0.5:
                if DEBUG:
                    print(f"  [pdf-row] cluster rechazado: CV={cv:.2f} > 0.5")
                continue

            # Score: más unique_vals mejor, más uniforme mejor
            score = len(unique_vals) * 10.0 - cv * 20.0
            if score > best_score:
                best_score = score
                best = deduped

        if best is None:
            return None

        if DEBUG:
            for d in sorted(best, key=lambda x: x["x_center"]):
                print(f"  [PDF] digit val={d['val']} "
                      f"x_center={d['x_center']:.0f}px "
                      f"y={d['y']:.0f}px h={d['h']:.0f}px")

        return best

    # ─────────────────────────────────────────────────────────────────
    # ANCHOR TEXTUAL
    # ─────────────────────────────────────────────────────────────────


    # ─────────────────────────────────────────────────────────────────
    # [V9-FIX-PDF] LOCALIZACIÓN NATIVA DEL WIDGET VÍA PyMuPDF
    # ─────────────────────────────────────────────────────────────────

    def _detect_srri_via_pdf_row(self, page, full_img: np.ndarray) -> Optional[int]:
        """
        [V9-FIX-PDF] Localiza el widget leyendo las posiciones de texto del PDF
        directamente con PyMuPDF. Sin Tesseract. Funciona incluso con texto blanco
        sobre fondo de color (la extracción de texto de PyMuPDF es independiente
        del color de renderizado).
        """
        h_img, w_img = full_img.shape[:2]
        page_h = page.rect.height

        # 1. Extraer todas las palabras con bounding boxes
        words = page.get_text("words")
        digit_words = []
        for ww in words:
            txt = str(ww[4]).strip()
            if txt in "1234567" and len(txt) == 1:
                x0, y0, x1, y1 = ww[0], ww[1], ww[2], ww[3]
                digit_words.append({
                    "val": int(txt),
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "cx": (x0 + x1) / 2.0,
                    "cy": (y0 + y1) / 2.0,
                    "h_pts": y1 - y0,
                    "w_pts": x1 - x0,
                })

        if len(digit_words) < 4:
            if DEBUG:
                print(f"  [pdf-row] solo {len(digit_words)} digitos, skip")
            return None

        # 2. Agrupar por Y
        med_h_pts = float(np.median([d["h_pts"] for d in digit_words]))
        tol_pts = med_h_pts * 1.5

        clusters: List[List[dict]] = []
        for dw in sorted(digit_words, key=lambda x: x["cy"]):
            placed = False
            for cl in clusters:
                cy_cl = float(np.median([c["cy"] for c in cl]))
                if abs(dw["cy"] - cy_cl) < tol_pts:
                    cl.append(dw)
                    placed = True
                    break
            if not placed:
                clusters.append([dw])

        # 3. Mejor cluster: max valores únicos + gaps uniformes
        best_cluster = None
        best_score = -1
        for cl in clusters:
            unique_vals = set(d["val"] for d in cl)
            n_unique = len(unique_vals)
            if n_unique < 4:
                continue
            sorted_cl = sorted(cl, key=lambda d: d["cx"])
            by_val: Dict[int, dict] = {}
            for d in sorted_cl:
                v = d["val"]
                if v not in by_val:
                    by_val[v] = d
                elif abs(d["cx"] - float(np.median([x["cx"] for x in sorted_cl]))) <                      abs(by_val[v]["cx"] - float(np.median([x["cx"] for x in sorted_cl]))):
                    by_val[v] = d
            deduped = sorted(by_val.values(), key=lambda d: d["cx"])
            if len(deduped) < 4:
                continue
            xs = [d["cx"] for d in deduped]
            gaps = np.diff(xs)
            if len(gaps) == 0:
                continue
            cv_gaps = float(np.std(gaps) / (np.mean(gaps) + 1e-9))

            # [V12-FIX-6] Rechazar cluster con gaps colapsados (x_center repetidos).
            # En KIIDs a dos columnas, los dígitos del widget (columna izquierda)
            # pueden aparecer en la capa de texto con el mismo x para varios valores.
            # min_gap=0 → polyfit produce m≈0 → all posiciones al mismo x → SRRI=1.
            min_gap_pts = float(gaps.min()) if len(gaps) > 0 else 0.0
            med_h = float(np.median([d["h_pts"] for d in deduped]))
            if min_gap_pts < 0.5 * med_h:
                if DEBUG:
                    print(f"  [pdf-row] cluster rechazado: min_gap={min_gap_pts:.1f}pts "
                          f"< 0.5×h={0.5*med_h:.1f}pts")
                continue
            # [V12-FIX-6b] Rechazar CV excesivo (no es un widget uniforme)
            if cv_gaps > 0.5:
                if DEBUG:
                    print(f"  [pdf-row] cluster rechazado: CV={cv_gaps:.2f} > 0.5")
                continue

            score = n_unique - cv_gaps * 2
            if score > best_score:
                best_score = score
                best_cluster = deduped

        if best_cluster is None or len(best_cluster) < 4:
            if DEBUG:
                print(f"  [pdf-row] no cluster valido")
            return None

        vals_c = [d["val"] for d in best_cluster]
        xs_pts = [d["cx"] for d in best_cluster]
        if DEBUG:
            print(f"  [pdf-row] cluster: vals={vals_c}")

        # 4. Polyfit: posicion_x = m * valor + c
        m_pts, c_pts = np.polyfit(vals_c, xs_pts, 1)
        if m_pts <= 0:
            return None

        scale = self.dpi / 72.0

        row_cy_pts = float(np.median([d["cy"] for d in best_cluster]))
        row_h_pts = float(np.median([d["h_pts"] for d in best_cluster]))
        strip_y0_pts = max(0, row_cy_pts - row_h_pts * 2.5)
        strip_y1_pts = min(page_h, row_cy_pts + row_h_pts * 2.5)

        sy0_px = max(0, int(strip_y0_pts * scale))
        sy1_px = min(h_img, int(strip_y1_pts * scale))

        if sy1_px - sy0_px < 5:
            return None

        strip_img = full_img[sy0_px:sy1_px, :]
        if DEBUG:
            cv2.imwrite(os.path.join(self.debug_dir, "pdf_strip.png"), strip_img)

        # 5. Medir color en 7 posiciones del grid
        hsv = cv2.cvtColor(strip_img, cv2.COLOR_BGR2HSV)
        h_s, w_s = strip_img.shape[:2]
        cell_w_px = max(4, int(abs(m_pts) * 0.85 * scale))

        sats, darks = [], []
        for digit_val in range(1, 8):
            cx_px = int((m_pts * digit_val + c_pts) * scale)
            half = cell_w_px // 2
            x1 = max(0, cx_px - half)
            x2 = min(w_s, cx_px + half)
            roi_cell = hsv[0:h_s, x1:x2]
            if roi_cell.size == 0:
                sats.append(0.0)
                darks.append(0.0)
                continue
            sats.append(float(np.mean(roi_cell[:, :, 1])))
            darks.append(255.0 - float(np.mean(roi_cell[:, :, 2])))

        if DEBUG:
            print(f"  [pdf-row] sats: {[round(s,1) for s in sats]}")
            print(f"  [pdf-row] darks: {[round(d,1) for d in darks]}")

        winner_idx = self._best_outlier(sats, darks)

        # Hue fallback para fondos ya saturados
        if winner_idx is None:
            sat_mean = float(np.mean(sats))
            sat_std = float(np.std(sats))
            if sat_mean > 60 and sat_std < 12:
                hues = []
                for digit_val in range(1, 8):
                    cx_px = int((m_pts * digit_val + c_pts) * scale)
                    half = cell_w_px // 2
                    x1 = max(0, cx_px - half)
                    x2 = min(w_s, cx_px + half)
                    roi_h_ch = hsv[0:h_s, x1:x2, 0]
                    if roi_h_ch.size == 0:
                        hues.append(90.0)
                        continue
                    h_rad = roi_h_ch.astype(float) * (np.pi / 90.0)
                    hue_mean = float(np.arctan2(np.mean(np.sin(h_rad)),
                                                np.mean(np.cos(h_rad))) * 90.0 / np.pi) % 180.0
                    hues.append(hue_mean)
                med_hue = float(np.median(hues))
                diffs = [min(abs(h - med_hue), 180.0 - abs(h - med_hue)) for h in hues]
                if DEBUG:
                    print(f"  [pdf-row] hue_diffs: {[round(d,1) for d in diffs]}")
                winner_idx = self._best_outlier(diffs, [0.0] * 7)

        if winner_idx is None:
            if DEBUG:
                print(f"  [pdf-row] scorer=None")
            return None

        srri = winner_idx + 1
        return srri if 1 <= srri <= 7 else None

    def _normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in text if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", text.lower())

    def _find_anchor(self, page: fitz.Page) -> Optional[float]:
        """
        [V7-FIX-1] Devuelve la coordenada Y del encabezado ESPECÍFICO de riesgos.
        Solo acepta bloques que contienen el patrón completo del título de sección,
        no cualquier bloque que mencione "riesgo".
        Patrones en orden de preferencia:
          1. "¿Qué riesgos corro..." / "What are the risks..." (patrón completo)
          2. "Indicador (resumido/sintético) de riesgo" / "Summary risk indicator"
          3. "Indicador de riesgo" (fallback)
        """
        raw = page.get_text("text")
        norm = self._normalize(raw)

        # Comprobar que la página tiene contenido de sección de riesgos
        has_risk_section = any(re.search(p, norm, re.DOTALL) for p in [
            r"que\s+riesgos?\s+corro",
            r"what\s+are\s+the\s+risks?",
            r"quels?\s+sont\s+les\s+risques?",
            r"welche\s+risiken?",
            r"indicador\s+(resumido|sintetico)\s+de\s+riesgo",
            r"summary\s+risk\s+indicator",
            r"synthetic\s+risk\s+indicator",
            r"risk\s+and\s+reward\s+indicator",
            r"indice\s+di\s+rischio",
        ])
        if not has_risk_section:
            return None

        blocks = page.get_text("blocks")

        # Patrones ESPECÍFICOS que identifican el encabezado de la sección SRRI.
        # Son más estrictos: deben matchear la mayor parte del bloque de texto.
        specific_patterns = [
            # Titulo completo (más fiable)
            r"que\s+riesgos?\s+corro",
            r"what\s+are\s+the\s+risks?",
            r"quels?\s+sont\s+les\s+risques?",
            r"welche\s+risiken?\s+",
            # Encabezado del indicador
            r"indicador\s+(resumido|sintetico|de\s+riesgo)",
            r"summary\s+risk\s+indicator",
            r"synthetic\s+risk\s+(indicator|and\s+reward)",
            r"risk\s+and\s+reward\s+indicator",
            r"risk\s+indicator",
            r"indice\s+di\s+rischio",
            r"risikoprofil",
        ]

        # Buscar bloques que matcheen los patrones específicos
        # IMPORTANTE: ordenar por Y para tomar el que esté más arriba en la página
        # pero verificar que esté DESPUÉS de la sección "¿Qué es este producto?"
        # Para ello, buscamos el Y mínimo de la sección de riesgos en el texto
        # de la página (no en el primer bloque con "riesgo").

        candidates = []
        for block in sorted(blocks, key=lambda b: b[1]):  # ordenar por Y
            bt = self._normalize(block[4])
            for pat in specific_patterns:
                if re.search(pat, bt):
                    candidates.append((block[1], block))
                    break

        if not candidates:
            return None

        # Tomar el candidato con Y más pequeño (el primero en la página)
        # que corresponda al inicio de la sección de riesgos
        best_y, best_block = candidates[0]
        return float(best_y)

    # ─────────────────────────────────────────────────────────────────
    # RENDER
    # ─────────────────────────────────────────────────────────────────

    def _render_page(self, page: fitz.Page) -> np.ndarray:
        pix = page.get_pixmap(dpi=self.dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        return img

    # ─────────────────────────────────────────────────────────────────
    # DEBUG HELPERS
    # ─────────────────────────────────────────────────────────────────

    def _draw_cells(self, image: np.ndarray, cells: List[dict],
                    winner: int, tag: str) -> None:
        if not DEBUG:
            return
        overlay = image.copy()
        h_img, w_img = image.shape[:2]
        for i, cell in enumerate(cells):
            digit = i + 1
            color = (0, 0, 255) if digit == winner else (0, 200, 0)
            x1, y1 = max(0, cell["x"]), max(0, cell["y"])
            x2 = min(w_img, cell["x"] + cell["w"])
            y2 = min(h_img, cell["y"] + cell["h"])
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
            cv2.putText(overlay, str(digit), (x1 + 2, y1 + 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        cv2.imwrite(os.path.join(self.debug_dir, f"{tag}_overlay.png"), overlay)
