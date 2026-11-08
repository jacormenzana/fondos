# proyecto1/core/oc_resolution.py
# -*- coding: utf-8 -*-
"""
Pegamento por fondo del ÚNICO escritor de `Ongoing_Charge_Recurrent` (FND-0034, 2026-10-17).

`cost_scale.resolve_ongoing_charge` decide entre candidatos ya preparados; este módulo los prepara a partir de lo que el pipeline
tiene en cada pase, también en los CACHED (donde el bloque de los extractores de coste se salta):

  * el FORMATO del documento (`cost_format_router.detect_kid_format`, un conteo de patrones: coste despreciable);
  * la comisión de gestión y los ACI EFECTIVOS (R-4): el valor de esta extracción si la hubo, y si no el guardado, porque eso es lo
    que COALESCE dejará en la base;
  * el candidato UCITS (la cifra OCF del documento, en ratio) solo si el formato es UCITS;
  * el candidato del parser (`parsed["Ongoing_Charge"]`, ratio).

Puro: recibe los valores guardados ya leídos (el pipeline hace el SELECT) y no toca la BD. Testeable sin pipeline.py ni core.io (R-7).
"""
from typing import Mapping, Optional, Sequence, Tuple

try:
    from core.cost_format_router import detect_kid_format
    from core.cost_scale import resolve_ongoing_charge
    from core.ucits_cost_extractor import ucits_ongoing_charge_ratio
except ImportError:                          # proyecto1/core ya en sys.path (los extractores se importan entre sí sin prefijo)
    from cost_format_router import detect_kid_format
    from cost_scale import resolve_ongoing_charge
    from ucits_cost_extractor import ucits_ongoing_charge_ratio
from shared.cost_rounding import refined_aci_pct

__all__ = ['resolve_fund_oc', 'refine_fund_aci']


def _effective(record: Mapping, key: str, stored: Optional[float]) -> Optional[float]:
    value = record.get(key)
    return value if value is not None else stored


def resolve_fund_oc(
    kid_text: Optional[str],
    parser_oc_ratio: Optional[float],
    record: Mapping,
    stored: Sequence[Optional[float]],
) -> Tuple[Optional[float], str, Optional[str]]:
    """
    (valor RATIO | None, fuente, formato) del gasto corriente que este pase puede escribir.

    `kid_text`: el texto que ven los extractores (`Fed_Text_For_Cost`, o el KIID crudo). `record`: el `fund_master_record` ya con los
    campos de coste de ESTA extracción, si la hubo. `stored`: (Management_Fee_Pct, ACI_RHP, ACI_1Y, Transaction_Cost_Pct, Entry_Fee_Pct_Max,
    Exit_Fee_Pct_Max, Performance_Fee_Pct) guardados, en PORCENTAJE ENTERO (los cuatro últimos pueden faltar: sin la operación el redondeo
    de la publicación no explica nada). None -> el UPSERT con COALESCE conserva el valor guardado.
    """
    fmt = detect_kid_format(kid_text) if kid_text else None
    ucits_oc = ucits_ongoing_charge_ratio(kid_text) if (fmt == 'UCITS_KIID' and kid_text) else None
    stored_mgmt, stored_rhp, stored_1y, stored_tc, stored_entry, stored_exit, stored_perf = (tuple(stored) + (None,) * 7)[:7]
    value, source = resolve_ongoing_charge(
        fmt,
        _effective(record, 'Management_Fee_Pct', stored_mgmt),
        ucits_oc,
        parser_oc_ratio,
        _effective(record, 'ACI_RHP', stored_rhp),
        _effective(record, 'ACI_1Y', stored_1y),
        _effective(record, 'Transaction_Cost_Pct', stored_tc),
        _effective(record, 'Entry_Fee_Pct_Max', stored_entry),
        _effective(record, 'Exit_Fee_Pct_Max', stored_exit),
        _effective(record, 'Performance_Fee_Pct', stored_perf),
    )
    return value, source, fmt


def refine_fund_aci(
    oc_ratio: Optional[float],
    record: Mapping,
    stored: Sequence[Optional[float]],
) -> Mapping[str, float]:
    """
    {columna: valor exacto de dos decimales} de los ACI que este pase puede afinar (FND-0034, decisión del propietario 2026-11-08).

    El ACI se imprime con UN decimal; si el guardado (el de esta extracción o, sin ella, el almacenado: R-4) es IGUAL al gasto corriente solo porque el redondeo de la publicación lo
    explica (gestión + operación, sin comisiones de entrada, salida ni de éxito: `shared.cost_rounding`), se guarda el valor exacto gestión + operación (0,3 impreso -> 0,34). Solo afina
    ese caso; todo lo demás conserva la cifra del documento. `oc_ratio`: el gasto corriente que quedará en la base (el resuelto en este pase o, sin él, el guardado), en RATIO.
    `stored`: (ACI_RHP, ACI_1Y, Transaction_Cost_Pct, Entry_Fee_Pct_Max, Exit_Fee_Pct_Max, Performance_Fee_Pct) guardados, en PORCENTAJE ENTERO. Puro: no toca la BD.
    """
    if oc_ratio is None:
        return {}
    s_rhp, s_1y, s_tc, s_entry, s_exit, s_perf = (tuple(stored) + (None,) * 6)[:6]
    tc, entry = _effective(record, 'Transaction_Cost_Pct', s_tc), _effective(record, 'Entry_Fee_Pct_Max', s_entry)
    exit_, perf = _effective(record, 'Exit_Fee_Pct_Max', s_exit), _effective(record, 'Performance_Fee_Pct', s_perf)
    out = {}
    for column, stored_value in (('ACI_RHP', s_rhp), ('ACI_1Y', s_1y)):
        current = _effective(record, column, stored_value)
        exact = refined_aci_pct(oc_ratio * 100.0, current, tc, entry, exit_, perf)
        if exact is not None:
            out[column] = exact
    return out
