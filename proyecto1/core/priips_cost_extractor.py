# proyecto1/core/priips_cost_extractor.py
# -*- coding: utf-8 -*-
"""
priips_cost_extractor.py — extractor de costes de KID PRIIPs.

BL-COST-4a (Sprint 2 S2-B): módulo nuevo.

Orquesta los módulos S2-A (cost_format_router, cost_table_parser, cost_cross_validator)
para extraer los campos de coste de un KID PRIIPs y devolver un dict listo para
upsert en fund_master + fund_cost_schedule.

NO reimplementa parsing de tablas, cross-validation ni detección de formato.
Su única lógica propia es:
  - Resolución de RHP numérico desde texto (_extract_rhp_years)
  - Conversión de escala ratio → % entero (_ratio_to_pct)
  - Ensamblado del dict de retorno
  - Construcción de _cost_schedule_rows (_build_schedule_rows)
  - Cálculo de Cost_Extraction_Quality (_assess_quality)
  - Heurística OC/ACI mismatch (_detect_oc_aci_mismatch)

Kill-switch: PRIIPS_COST_EXTRACTION_ENABLED = False (config v19.1).
  Si False → extract_priips_costs retorna {} inmediatamente.

Reglas de robustez (Principio #1, DRY #2):
  R-5: word boundary \\b en todo patrón regex nuevo.
  R-6: ventanas acotadas y lazy en todo patrón nuevo.
  R-8: AST validation tras cada escritura.
  Sin efectos secundarios en import.
  Ninguna excepción sale al caller.
"""

import re
import logging
from typing import Optional, List, Dict, Any

# ---------------------------------------------------------------------------
# Dependencias S2-A
# ---------------------------------------------------------------------------
from cost_format_router   import detect_kid_format, detect_kid_currency
from cost_table_parser    import parse_costs_over_time, parse_costs_composition
from cost_cross_validator import validate_pct_eur, ValidationResult

# ---------------------------------------------------------------------------
# Config con fallback aislado (mismo patrón que cost_cross_validator.py)
# Los 3 símbolos existen en config v19.1 (producción). El fallback solo actúa
# en entornos aislados (tests sin config en path).
# ---------------------------------------------------------------------------
try:
    from config import (
        PRIIPS_INVESTMENT_BASE,
        COST_CROSS_VALIDATION_TOLERANCE_PCT,
        PRIIPS_COST_EXTRACTION_ENABLED,
    )
except ImportError:
    PRIIPS_INVESTMENT_BASE: float          = 10000.0
    COST_CROSS_VALIDATION_TOLERANCE_PCT: float = 0.0005   # = config v19.1
    PRIIPS_COST_EXTRACTION_ENABLED: bool   = False         # = config v19.1

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes de módulo
# ---------------------------------------------------------------------------

# Source único para filas de schedule (P-7).
# Todas las filas de _cost_schedule_rows proceden de parse_costs_over_time.
_SCHEDULE_SOURCE = 'PRIIPS_COSTS_OVER_TIME'

# Valores permitidos en la columna Source de fund_cost_schedule (P-7).
# Refleja el CHECK del schema (3 valores), NO el tuple de config (5 valores).
# ⚠ NO usar 'PRIIPS_COMPOSITION' ni 'PRIIPS_TEXT' como Source de filas de schedule:
#   el CHECK del schema los rechaza → IntegrityError en upsert_cost_schedule.
_SCHEDULE_SOURCE_ALLOWED = ('PRIIPS_COSTS_OVER_TIME', 'UCITS_DERIVED', 'MANUAL')

# Patrón RHP numérico (R-5 word boundary, R-6 ventana lazy acotada).
# Captura el número y la unidad tras "período de mantenimiento recomendado: X años/meses"
# y la variante inglesa. Busca en una ventana de 60 chars tras la etiqueta.
_RHP_VALUE_PATTERN = re.compile(
    r'(?:per[ií]odo\s+de\s+mantenimiento\s+recomendado|recommended\s+holding\s+period)'
    r'\s*[:\-]?\s*'
    r'(\d+)\s*(a[ñn]os?|years?|mes(?:es)?|months?)\b',
    re.IGNORECASE,
)

# Umbral de discrepancia grave — espejo de cost_cross_validator._SEVERE_DISCREPANCY_THRESHOLD
_SEVERE_DISCREPANCY_THRESHOLD = 0.005   # 50bp en ratio decimal

# Umbrales heurística OC/ACI mismatch (P-3), todos en ratio decimal
_OC_ACI_NEAR_PP = 0.0010   # 0.10pp: el valor en BD "se parece" al ACI_RHP
_OC_TER_FAR_PP  = 0.0030   # 0.30pp: el valor en BD difiere del TER reconstruido


# ===========================================================================
# Funciones privadas
# ===========================================================================

def _ratio_to_pct(x: Optional[float]) -> Optional[float]:
    """
    Convierte ratio decimal a porcentaje entero.
    Ejemplo: 0.0525 → 5.25.
    None → None. (P-4)
    """
    if x is None:
        return None
    return round(x * 100.0, 4)


def _extract_rhp_years(text: str) -> Optional[float]:
    """
    Resuelve el RHP (Período de Mantenimiento Recomendado) numérico desde texto.
    Años: devuelve el valor directamente.
    Meses: convierte a años con round(meses/12, 4).
    Retorna None si no se halla. (P-1, P-5)
    """
    m = _RHP_VALUE_PATTERN.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit  = m.group(2).lower()
    if 'mes' in unit or 'month' in unit:
        return round(value / 12.0, 4)
    return float(value)


def _pick_aci_for_horizon(
    rows: List[dict],
    target_years: Optional[float],
    want_rhp: bool,
) -> Optional[float]:
    """
    Selecciona el aci_pct (ratio decimal) de la fila que coincide con el horizonte pedido.
    Si want_rhp=True, prioriza la fila con is_rhp=True; si no existe, busca por target_years.
    Tolerancia de match de horizonte: ±0.01 años.
    Retorna None si no se encuentra.
    """
    if not rows:
        return None
    # Prioridad: fila RHP si se pide
    if want_rhp:
        for r in rows:
            if r.get('is_rhp'):
                return r.get('aci_pct')
    # Búsqueda por horizonte numérico
    if target_years is not None:
        for r in rows:
            hy = r.get('horizon_years')
            if hy is not None and hy >= 0 and abs(hy - target_years) <= 0.01:
                return r.get('aci_pct')
    return None


def _pick_eur_for_horizon(
    rows: List[dict],
    target_years: Optional[float],
    want_rhp: bool,
) -> Optional[float]:
    """
    Igual que _pick_aci_for_horizon pero devuelve total_cost_eur.
    """
    if not rows:
        return None
    if want_rhp:
        for r in rows:
            if r.get('is_rhp'):
                return r.get('total_cost_eur')
    if target_years is not None:
        for r in rows:
            hy = r.get('horizon_years')
            if hy is not None and hy >= 0 and abs(hy - target_years) <= 0.01:
                return r.get('total_cost_eur')
    return None


def _norm_existing_oc(existing_oc: Optional[float]) -> Optional[float]:
    """
    Normaliza el OC legacy a ratio decimal para comparación interna.
    Heurística de escala (espejo inverso de cost_format_signals.py:291 oc_pct):
      - Si existing_oc >= 0.5 → se asume que está en % → dividir entre 100.
      - Si existing_oc < 0.5  → se asume que ya es ratio.
    # DRY-SYNC: cost_format_signals.py:291 (oc_pct)
    """
    if existing_oc is None:
        return None
    if existing_oc >= 0.5:
        return existing_oc / 100.0
    return existing_oc


def _detect_oc_aci_mismatch(
    existing_oc: Optional[float],
    oc_norm: Optional[float],
    ter_recon_ratio: Optional[float],
    aci_rhp_ratio: Optional[float],
) -> bool:
    """
    Devuelve True si el OC legacy en BD parece ser ACI (no TER):
    está cerca del ACI_RHP y lejos del TER reconstruido.
    Todos los argumentos comparables en RATIO decimal.
    Conservador: ante cualquier None → False. (P-3)
    """
    if existing_oc is None or oc_norm is None:
        return False
    if ter_recon_ratio is None or aci_rhp_ratio is None:
        return False
    near_aci = abs(oc_norm - aci_rhp_ratio) <= _OC_ACI_NEAR_PP   # se parece al ACI
    far_ter  = abs(oc_norm - ter_recon_ratio) >  _OC_TER_FAR_PP  # difiere del TER
    return near_aci and far_ter


def _build_schedule_rows(
    rows: List[dict],
    rhp_years: Optional[float],
    isin: str,
) -> List[dict]:
    """
    Construye _cost_schedule_rows a partir de parse_costs_over_time, resolviendo:
      - RHP a valor numérico (P-1, Decisión B).
      - Fusión de colisiones de PK (mismo Horizon_Years) marcando Is_RHP=1.
      - Conversión de escala: aci_pct ratio → Annual_Impact_Pct % entero (P-4).
      - Validación defensiva de Source (P-7).
    """
    by_horizon: Dict[float, dict] = {}

    for r in rows:
        hy  = r.get('horizon_years')
        rhp = bool(r.get('is_rhp'))

        # Resolver RHP a su valor numérico (P-1, Decisión B)
        if rhp or hy == -1.0:
            if rhp_years is None:
                _log.info(
                    "[BL-COST-4a] %s: fila RHP sin Cost_RHP_Years resuelto; fila descartada",
                    isin,
                )
                continue
            hy = rhp_years

        # Filtrar horizontes no válidos para el CHECK de schema: 0 < hy <= 50
        if hy is None or not (0 < hy <= 50):
            continue

        eur = r.get('total_cost_eur')
        aci = r.get('aci_pct')   # ratio decimal

        row: dict = {
            'Horizon_Years': round(hy, 4),
            'Is_RHP':        1 if (rhp or (rhp_years is not None and abs(hy - rhp_years) <= 0.01)) else 0,
            'Source':        _SCHEDULE_SOURCE,
        }
        if eur is not None:
            row['Total_Costs_EUR'] = eur
        if aci is not None:
            row['Annual_Impact_Pct'] = _ratio_to_pct(aci)
        # Total_Costs_Pct: EUR acumulado / base en % entero (coherencia con columnas nuevas P-4)
        if eur is not None:
            row['Total_Costs_Pct'] = _ratio_to_pct(eur / PRIIPS_INVESTMENT_BASE)

        # Fusión de colisión de PK (P-1): mismo Horizon_Years
        key = row['Horizon_Years']
        if key in by_horizon:
            prev = by_horizon[key]
            # Marcar Is_RHP=1 si alguna de las dos filas es RHP
            prev['Is_RHP'] = max(prev['Is_RHP'], row['Is_RHP'])
            # Completar campos vacíos con los de la fila nueva (sin sobreescribir)
            for k, v in row.items():
                prev.setdefault(k, v)
        else:
            by_horizon[key] = row

    # Validación defensiva de Source (P-7): descartar filas con Source no permitido
    valid_rows = [r for r in by_horizon.values() if r['Source'] in _SCHEDULE_SOURCE_ALLOWED]
    discarded  = len(by_horizon) - len(valid_rows)
    if discarded:
        _log.warning(
            "[BL-COST-4a] %s: %d fila(s) de schedule descartadas por Source no permitido",
            isin, discarded,
        )
    return valid_rows


def _assess_quality(
    vr_rhp: ValidationResult,
    vr_1y: ValidationResult,
    aci_rhp_final: Optional[float],
    aci_1y_final: Optional[float],
    comp: dict,
    over_time: List[dict],
    schedule_source_used: Optional[str],
) -> str:
    """
    Calcula Cost_Extraction_Quality según §3 del traspaso S2-B.
    Evaluado en orden; se asigna el PRIMER valor cuyo criterio se cumple.

    Valores posibles:
        'HIGH'         — ancla tiene % y EUR con cross-validation OK (≤5bp)
        'MEDIUM_CROSS' — ancla tiene % y EUR con discrepancia leve (5–50bp)
        'MEDIUM_EUR'   — ancla solo tiene EUR (PCT_ONLY → EUR_ONLY invertido)
        'MEDIUM_PCT'   — ancla solo tiene % 
        'LOW'          — discrepancia grave / solo texto plano / datos parciales
        'NONE'         — no se extrajo ningún dato de coste
    """
    has_over_time  = bool(over_time)
    has_comp       = bool(comp)

    # Regla 1: sin datos en absoluto
    if not has_over_time and not has_comp:
        return 'NONE'

    # Ancla en 1Y: única cross-val % vs EUR semánticamente válida (ACI anual vs EUR ~anual).
    # vr_rhp compara ACI anual contra EUR ACUMULADO → discrepancia espuria a RHP>1Y (BL-COST fix).
    anchor = vr_1y if vr_1y.status != 'NONE' else vr_rhp

    # Regla 3: HIGH — ambos datos, cross-validation OK
    if anchor.status == 'OK':
        return 'HIGH'

    # Regla 4: MEDIUM_CROSS — discrepancia leve (validated_pct no es None)
    if anchor.status == 'DISCREPANCY' and anchor.validated_pct is not None:
        return 'MEDIUM_CROSS'

    # Regla 5: MEDIUM_EUR — solo EUR disponible (ancla EUR_ONLY)
    if anchor.status == 'EUR_ONLY':
        return 'MEDIUM_EUR'

    # Regla 6: MEDIUM_PCT — solo % disponible (ancla PCT_ONLY)
    if anchor.status == 'PCT_ONLY':
        return 'MEDIUM_PCT'

    # Regla 7: LOW — discrepancia grave, texto plano sin ancla, datos parciales
    # (anchor.status == 'DISCREPANCY' con validated_pct=None, o NONE en ambos
    #  pero hay algún dato de composición, o única fuente fue PLAIN_TEXT)
    if has_over_time or has_comp:
        return 'LOW'

    return 'NONE'


# ===========================================================================
# API pública
# ===========================================================================

def extract_priips_costs(
    text: str,
    isin: str,
    existing_oc:    Optional[float] = None,    # Ongoing_Charge_Recurrent actual en BD (escala BD)
    existing_entry: Optional[float] = None,    # Entry_Fee_Pct actual en BD
    existing_exit:  Optional[float] = None,    # Exit_Fee_Pct actual en BD
) -> Dict[str, Any]:
    """
    Extrae los campos de coste de un KID PRIIPs a partir del texto concatenado
    (Raw_KIID_Text + DLA2_Table_Text). Orquesta los módulos S2-A; no reimplementa parsing.

    Contrato:
      - Respeta PRIIPS_COST_EXTRACTION_ENABLED (kill-switch). Si False → retorna {}.
      - Ninguna excepción sale al caller (try/except global → dict parcial + quality 'LOW').
      - Devuelve SOLO las claves extraídas con éxito (claves ausentes = no extraído),
        EXCEPTO Cost_Extraction_Quality, KID_Format y _cost_schedule_rows, que
        siempre están presentes.
      - Escala de salida: porcentaje entero para *_Pct / ACI_* (P-4). EUR absoluto sin convertir.

    Claves posibles del dict de retorno:
      KID_Format               str            (siempre)
      KID_Currency             str | None
      Cost_Extraction_Quality  str            (siempre; 'HIGH'|'MEDIUM_CROSS'|'MEDIUM_EUR'|
                                               'MEDIUM_PCT'|'LOW'|'NONE')
      Cost_RHP_Years           float | None
      Entry_Fee_Pct_Max        float | None   (% entero)
      Exit_Fee_Pct_Max         float | None   (% entero)
      Management_Fee_Pct       float | None   (% entero)
      Transaction_Cost_Pct     float | None   (% entero)
      Performance_Fee_Pct      float | None   (% entero)
      ACI_1Y                   float | None   (% entero)
      ACI_RHP                  float | None   (% entero)
      Ongoing_Charge_Recurrent float | None   (% entero; SOLO si existing_oc is None y TER OK)
      _cost_schedule_rows      List[dict]     (siempre; puede ser [])
      _oc_aci_mismatch         bool           (solo si se detecta mezcla TER/ACI con OC existente)

    Hallazgo §4.4: cross-validation % ↔ EUR solo es válida a horizonte 1 año
    (EUR acumulado ≈ ACI × base solo cuando horizonte = 1 año). Para RHP > 1 año,
    cruzar ACI_RHP con EUR_RHP daría discrepancia espuria. Por eso:
      - El ancla de cross-validation es el horizonte 1Y (vr_1y) cuando existe.
      - ACI_RHP se toma directamente del aci_pct de la fila RHP (ratio→%), salvo
        cuando rhp_years == 1.0, donde se usa el validated_pct de vr_rhp.
      - Para RHP < 1Y (ej. FR0000989626, 3m): el EUR acumulado / base ≈ ACI
        porque el periodo es corto; la cross-validation funciona como HIGH.

    Limitación conocida de texto plano (§4.1):
      En ruta PLAIN_TEXT, parse_costs_over_time devuelve filas con total_cost_eur
      y aci_pct idénticos para todas las columnas (bug de duplicación). La 2ª
      columna no es fiable. Con DLA2 activo, este comportamiento desaparece.
    """
    # --- 0. KILL-SWITCH (primera línea ejecutable) ---
    if not PRIIPS_COST_EXTRACTION_ENABLED:
        return {}

    out: Dict[str, Any] = {}

    try:
        # --- A. Formato y moneda (siempre se intenta) ---
        out['KID_Format'] = detect_kid_format(text)
        currency = detect_kid_currency(text)
        if currency:
            out['KID_Currency'] = currency

        # Si no es PRIIPS_KID, este extractor no aplica
        if out['KID_Format'] != 'PRIIPS_KID':
            out['Cost_Extraction_Quality'] = 'NONE'
            out['_cost_schedule_rows']     = []
            return out

        # --- B. RHP numérico (necesario para P-1 y como campo de retorno) ---
        rhp_years = _extract_rhp_years(text)
        if rhp_years is not None:
            out['Cost_RHP_Years'] = rhp_years

        # --- C. Tabla "costes a lo largo del tiempo" ---
        over_time = parse_costs_over_time(text)
        schedule_source_used: Optional[str] = None
        if over_time:
            schedule_source_used = over_time[0].get('source')

        # ACI 1Y (None si RHP < 1 o no hay columna 1Y) — P-5
        aci_1y_ratio = _pick_aci_for_horizon(over_time, target_years=1.0, want_rhp=False)
        eur_1y       = _pick_eur_for_horizon(over_time, target_years=1.0, want_rhp=False)

        # ACI RHP (ancla de calidad) — preferir fila is_rhp; si no, fila == rhp_years
        aci_rhp_ratio = _pick_aci_for_horizon(over_time, target_years=rhp_years, want_rhp=True)
        eur_rhp       = _pick_eur_for_horizon(over_time, target_years=rhp_years, want_rhp=True)

        # Cross-validation del ancla 1Y y del RHP.
        # NOTA §4.4: solo es semánticamente correcta a 1 año. Para RHP > 1,
        # validate_pct_eur(ACI_RHP, EUR_RHP) produce discrepancia espuria.
        # Se calcula igualmente para que _assess_quality use vr_1y como ancla.
        vr_1y  = validate_pct_eur(aci_1y_ratio,  eur_1y,  base=PRIIPS_INVESTMENT_BASE)
        vr_rhp = validate_pct_eur(aci_rhp_ratio, eur_rhp, base=PRIIPS_INVESTMENT_BASE)

        # ACI 1Y: usar validated_pct de vr_1y (horizonte 1Y → cross-val válida)
        aci_1y_final = vr_1y.validated_pct if vr_1y.status != 'NONE' else None

        # ACI RHP: tomar directamente el ratio (§4.4), salvo cuando RHP == 1Y
        if rhp_years is not None and abs(rhp_years - 1.0) <= 0.01:
            # RHP = 1 año → cross-validation es legítima
            aci_rhp_final = vr_rhp.validated_pct if vr_rhp.status != 'NONE' else None
        else:
            # RHP != 1Y → tomar el aci_pct crudo (no pasar por validated_pct)
            aci_rhp_final = aci_rhp_ratio

        # P0-ACI-GUARD: ACI values > 25% are parser bleed (scenario section
        # percentages captured instead of cost ACI). Confirmed root cause:
        # LU0256846568 (79.8%), LU1575199994 (71.4%) — CHECK constraint
        # ACI_RHP <= 25 was violated, blocking publish_fund entirely.
        # Reject at ratio level (0.25 == 25%) — better NULL than wrong value.
        _MAX_ACI_RATIO = 0.25
        if aci_1y_final is not None and aci_1y_final <= _MAX_ACI_RATIO:
            out['ACI_1Y'] = _ratio_to_pct(aci_1y_final)
        if aci_rhp_final is not None and aci_rhp_final <= _MAX_ACI_RATIO:
            out['ACI_RHP'] = _ratio_to_pct(aci_rhp_final)

        # --- D. Tabla "composición de los costes" ---
        comp = parse_costs_composition(text)

        # Entry/Exit MAX: preferir *_max_pct; fallback a *_fee_pct
        # P0-FEE-GUARD: Entry/Exit > 25% is a parser error (mis-assigned value).
        # Confirmed: LU0823416762 produced Entry_Fee_Pct_Max > 25, blocking
        # publish_fund. _guarded_pct in the composition parser caps at 25% but
        # the ratio threshold is tight — add a final boundary here as backstop.
        # Schema CHECK: Entry_Fee_Pct_Max <= 25 (percent form).
        _MAX_FEE_RATIO = 0.25
        entry_max = comp.get('entry_fee_max_pct', comp.get('entry_fee_pct'))
        exit_max  = comp.get('exit_fee_max_pct',  comp.get('exit_fee_pct'))
        if entry_max is not None and entry_max <= _MAX_FEE_RATIO:
            out['Entry_Fee_Pct_Max'] = _ratio_to_pct(entry_max)
        if exit_max is not None and exit_max <= _MAX_FEE_RATIO:
            out['Exit_Fee_Pct_Max']  = _ratio_to_pct(exit_max)

        mgmt = comp.get('management_fee_pct')
        tran = comp.get('transaction_cost_pct')
        perf = comp.get('performance_fee_pct')
        if mgmt is not None:
            out['Management_Fee_Pct']   = _ratio_to_pct(mgmt)
        if tran is not None:
            out['Transaction_Cost_Pct'] = _ratio_to_pct(tran)
        if perf is not None:
            out['Performance_Fee_Pct']  = _ratio_to_pct(perf)

        # --- E. TER reconstruido y gestión de Ongoing_Charge_Recurrent (P-3) ---
        ter_recon_ratio: Optional[float] = None
        if mgmt is not None:
            ter_recon_ratio = mgmt + (tran or 0.0)

        if ter_recon_ratio is not None:
            if existing_oc is None:
                # COALESCE-compatible: rellenar hueco con TER puro
                out['Ongoing_Charge_Recurrent'] = _ratio_to_pct(ter_recon_ratio)
            else:
                oc_norm = _norm_existing_oc(existing_oc)
                if _detect_oc_aci_mismatch(existing_oc, oc_norm, ter_recon_ratio, aci_rhp_final):
                    out['_oc_aci_mismatch'] = True
                    _log.info(
                        "[BL-COST-4a][OC-ACI] %s: BD OC parece ACI (%.4f) != TER recon (%.4f); "
                        "diferido a BL-COST-5",
                        isin, oc_norm or -1.0, ter_recon_ratio,
                    )
                # Si no hay mismatch y existing_oc no es None → no se toca (COALESCE)

        # --- F. _cost_schedule_rows (P-1, P-7, P-4) ---
        out['_cost_schedule_rows'] = _build_schedule_rows(over_time, rhp_years, isin)

        # --- G. Calidad (§3) ---
        out['Cost_Extraction_Quality'] = _assess_quality(
            vr_rhp=vr_rhp,
            vr_1y=vr_1y,
            aci_rhp_final=aci_rhp_final,
            aci_1y_final=aci_1y_final,
            comp=comp,
            over_time=over_time,
            schedule_source_used=schedule_source_used,
        )

        return out

    except Exception as exc:
        _log.warning(
            "[BL-COST-4a] %s: fallo en extracción (%s); retorno parcial LOW",
            isin, exc,
        )
        out.setdefault('KID_Format', 'UNKNOWN')
        out.setdefault('Cost_Extraction_Quality', 'LOW')
        out.setdefault('_cost_schedule_rows', [])
        return out
