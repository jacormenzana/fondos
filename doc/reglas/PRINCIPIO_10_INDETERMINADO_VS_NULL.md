# PRINCIPIO #10: Valor categórico "indeterminado por naturaleza" ≠ NULL

**Fecha:** 11 de julio de 2026
**Prioridad:** CRÍTICA (deriva de Principio #1 COALESCE y Principio #9 Consistencia semántica)
**Estado:** Implementado para `Asset_Currency` (centinela `MCY`); auditoría de atributos hermanos completada.

---

## REGLA FUNDAMENTAL

Un atributo extraído tiene **dos causas distintas** para no contener una divisa/categoría única, y **no deben confundirse**:

1. **Indeterminado por naturaleza** — el KIID/nombre declara **explícitamente** que el fondo NO tiene un valor único (p.ej. un mandato multi-divisa: *"euros u otras divisas"*). Es una propiedad **positiva y conocida** del fondo. → **valor categórico centinela** (identificable, aislable, consultable).
2. **No descubierto** — no hay señal suficiente para determinar el valor; puede que exista uno único y aún no se haya extraído. Es **ausencia de conocimiento**. → **`NULL`**.

Colapsar ambos en `NULL` **destruye información**: hace imposible distinguir *"sabemos que es multi-divisa"* de *"no sabemos"*, y mezcla dos poblaciones que requieren tratamiento distinto aguas abajo (scoring, filtros, FX-mismatch).

---

## RAZÓN

- **Aislamiento de poblaciones.** Un fondo multi-divisa por diseño es una decisión de cartera legítima (diversificación cambiaria), no un dato faltante que reparar. Debe poder consultarse (`WHERE Asset_Currency = 'MCY'`) y excluirse de reglas que asumen divisa única (p.ej. detección de descalce divisa-clase).
- **Compatibilidad con COALESCE (Principio #1).** Un extractor que devuelve `None` **no puede** sobrescribir un valor previo (COALESCE lo preserva). Un extractor que devuelve el centinela **sí** es un valor no-nulo y sobrescribe correctamente un `EUR` heredado y ya no vigente en el próximo ciclo. El centinela hace que "es multi-divisa" sea una afirmación **persistible**, no un silencio.
- **Root cause (Principio #2).** Emitir el centinela desde el clasificador es la corrección estructural. La alternativa —dejar `NULL` y parchear con SQL o listas de ISINs— es un parche de síntoma prohibido.

---

## IMPLEMENTACIÓN DE REFERENCIA: `Asset_Currency` → `MCY`

Centinela: **`MCY`** ("Multi-CurrencY"). Código de 3 letras (misma forma que las divisas ISO reales `EUR`/`USD`/…), **no colisiona** con ningún ISO-4217 real, seguro frente a `normalize_casing` (se emite en mayúsculas y pasa sin remapear).

**Único punto de definición (R-1):** `proyecto1/core/classify_utils.py`
```python
ASSET_CURRENCY_MULTI = "MCY"
```

**Emisión — dos señales:**

1. `detect_asset_currency_from_name()` — token multi-divisa explícito en el nombre
   (`multicurrency` / `multi-currency` / `multidivisa`) → devuelve `MCY` antes del
   barrido de divisa única.
2. `detect_asset_currency_from_kiid_text()` — cuando el guard
   `_KIID_MULTI_CCY_CONTINUATION` detecta una continuación multi-divisa
   (*"euros u otras divisas"*, *"o en otras monedas"*) y **no** hay una divisa
   dominante limpia, devuelve `MCY` (antes devolvía `None`). Retorno ternario:
   `divisa única` / `MCY` / `None`.

**Consumo defensivo:** `detect_fx_share_class_mismatch()` trata `MCY` como
"no es divisa única" → devuelve `False` (un mandato multi-divisa no puede generar
un descalce divisa-clase limpio).

**Capa de intención (documentación, no enforcement):** `shared/config.py`
```python
DOMAIN_VALUES["Asset_Currency"] = ["EUR", "USD", "GBP", "CHF", "JPY", "CNH", "MCY"]
```
Nota: las columnas de tipo CODE quedan excluidas de `ALLOWED_VALUES_BY_COLUMN`, por lo
que esta entrada es documentación de dominio, no validación dura.

**Blast radius:** `Asset_Currency` se consume solo en P1 (verificado). P2/P3 no lo leen.

---

## AUDITORÍA DE ATRIBUTOS HERMANOS

Se auditó cada atributo categórico por el mismo defecto *"diversidad explícita → NULL"*.
**Conclusión: solo `Asset_Currency` carecía del centinela.** Los demás **ya** expresan
la indeterminación con un valor categórico propio y NO deben recibir un centinela nuevo
(inventarlo violaría Principio #2 root-cause / no-parches):

| Atributo | ¿Expresa "diverso por naturaleza"? | Cómo |
|----------|-----------------------------------|------|
| **Asset_Currency** | ❌ **faltaba** → **corregido con `MCY`** | antes colapsaba a `NULL` |
| Geography | ✅ ya | valor categórico **`Global`** |
| Sector_Focus | ✅ ya | `Investment_Focus = 'Broad'` (sin sector concreto) |
| Type | ✅ ya | valor **`Unconstrained`** / "…Flexible…" |
| Hedging_Policy | ✅ N/A | binario (Hedged/Unhedged); sin estado "diverso" |

**Regla de extensión futura:** al añadir cualquier atributo categórico extraído, decidir
explícitamente si admite un estado *"diverso/indeterminado por naturaleza"*. Si lo admite
y no existe ya un valor que lo capture, definir un centinela categórico (no `NULL`),
declararlo en `DOMAIN_VALUES`, emitirlo desde el clasificador (nunca por SQL) y proteger
los consumidores que asumen valor único.

---

## CONSECUENCIAS DE VIOLACIÓN

1. Devolver `NULL` para un fondo cuyo KIID **declara** diversidad → pérdida de información,
   población no aislable, y el valor heredado (posiblemente obsoleto) **sobrevive** por COALESCE.
2. Inventar un centinela para un atributo que **ya** tiene su valor de diversidad (p.ej. un
   segundo "Global" para Geography) → duplicación semántica, viola Principio #9 (INTRA-atributo).
3. Poblar el centinela por SQL ad-hoc en vez del clasificador → viola Principio #2 y #7.

---

**FIN PRINCIPIO #10**

*Implementación 2026-07-11: `MCY` en `classify_utils.py`; tests en
`test_asset_currency.py` y `test_incident_fixes_20260706.py`.*
