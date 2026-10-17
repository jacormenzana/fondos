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

__all__ = ['resolve_fund_oc']


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
    campos de coste de ESTA extracción, si la hubo. `stored`: (Management_Fee_Pct, ACI_RHP, ACI_1Y) guardados, en PORCENTAJE ENTERO.
    None -> el UPSERT con COALESCE conserva el valor guardado.
    """
    fmt = detect_kid_format(kid_text) if kid_text else None
    ucits_oc = ucits_ongoing_charge_ratio(kid_text) if (fmt == 'UCITS_KIID' and kid_text) else None
    stored_mgmt, stored_rhp, stored_1y = (tuple(stored) + (None, None, None))[:3]
    value, source = resolve_ongoing_charge(
        fmt,
        _effective(record, 'Management_Fee_Pct', stored_mgmt),
        ucits_oc,
        parser_oc_ratio,
        _effective(record, 'ACI_RHP', stored_rhp),
        _effective(record, 'ACI_1Y', stored_1y),
    )
    return value, source, fmt
