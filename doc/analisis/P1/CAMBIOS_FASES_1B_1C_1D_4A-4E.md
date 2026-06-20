# CAMBIOS A APLICAR — Fases 1B, 1C, 1D, 4A-4E

**Orden de aplicación:** restantes.py → pipeline.py → classify_utils.py
**Tras aplicar:** Limpiar `__pycache__` y re-ejecutar bloque RESTANTES.

---

## FICHERO 1: `blocks/restantes.py`

### Cambio 1D — Fix regresión Theme + Cambio 1B — Aceptar srri_parsed

Sustituir la función `classify_fund` completa. Los cambios son:

1. Añadir parámetro `srri_parsed=None`
2. Tras delegación exitosa, enriquecer Theme si el bloque no lo asigna
3. Usar `srri_parsed` en Capa 3 en lugar del regex `X/7`
4. En el fallback, preservar `nature_canonical` detectada (no forzar "Restantes")

```python
def classify_fund(
    fund_name: str,
    kiid_text: Optional[str],
    benchmark_declared: Optional[str] = None,
    srri_parsed: Optional[int] = None,            # ← NUEVO (Fase 1B)
) -> Dict[str, Optional[str]]:
    """
    Clasifica un fondo del bloque Restantes.

    1. Detecta Fund_Nature con las 3 capas (KIID → nombre → SRRI).
    2. Delega al bloque primario para caracterizacion completa.
    3. Si no se puede determinar: tipificacion minima + atributos universales.
    """
    name_l = fund_name.lower() if isinstance(fund_name, str) else ""
    text_l = kiid_text.lower() if isinstance(kiid_text, str) else ""

    # ── Capa 1 — texto KIID (ventana adaptativa DDF/KIID)
    nature_raw = detect_nature_from_kiid(kiid_text or "")

    # ── Capa 2: nombre del fondo
    if not nature_raw:
        nature_raw = detect_nature_from_name(name_l)

    # ── Capa 2.5: benchmark declarado
    if not nature_raw and benchmark_declared:
        try:
            from core.benchmark_normalizer import normalize_benchmark
            norm = normalize_benchmark(benchmark_declared)
            if norm:
                _bench_nature_map = {
                    "Equity":       "Renta Variable",
                    "Fixed Income": "RF_Flexible",
                    "Rate":         "Monetario",
                    "Commodity":    "Alternativo",
                    "Mixed":        "Mixtos",
                }
                nature_raw = _bench_nature_map.get(norm.asset_class)
        except Exception:
            pass

    # ── Resolver RF pendiente
    if nature_raw == "_RF_pending":
        nature_raw = resolve_rf_subtype(name_l, kiid_text or "")

    # ── Capa 3: SRRI como árbitro — USAR SRRI PARSEADO (Fase 1B) ────────
    if not nature_raw:
        # Primero intentar srri_parsed (de kiid_parser, más fiable)
        srri = srri_parsed
        # Fallback: regex simple para KIIDs clásicos
        if srri is None:
            import re
            m = re.search(r"\b([1-7])\s*/\s*7\b", text_l)
            srri = int(m.group(1)) if m else None

        if srri == 1:
            nature_raw = "Monetario"
        elif srri is not None and srri >= 5:
            nature_raw = "Renta Variable"
        elif srri == 2:
            nature_raw = "_RF_pending"

    # ── Resolver RF pendiente (puede venir de Capa 3)
    if nature_raw == "_RF_pending":
        nature_raw = resolve_rf_subtype(name_l, kiid_text or "")

    # ── Naturaleza canónica
    nature_canonical = _NATURE_CANONICAL.get(nature_raw, "Restantes") \
                       if nature_raw else "Restantes"

    # ── Delegar al bloque primario ────────────────────────────────────────
    block_name = _NATURE_TO_BLOCK.get(nature_raw)
    if not block_name:
        # Intentar también con la clave canónica
        block_name = _NATURE_TO_BLOCK.get(nature_canonical)

    if block_name:
        try:
            block_mod = importlib.import_module(f"blocks.{block_name}")
            result = block_mod.classify_fund(fund_name, kiid_text)
            result["Fund_Nature"] = nature_canonical

            # ── Enriquecer Theme si el bloque no lo asigna (Fase 1D) ─────
            if not result.get("Theme"):
                result["Theme"] = _detect_theme(name_l) or "Core/General"

            return apply_semantic_validation(result, fund_name)
        except Exception as exc:
            logger.error(
                "[%s] Delegación a bloque '%s' fallida: %s: %s",
                fund_name, block_name, type(exc).__name__, exc,
            )

    # ── Fallback: tipificacion minima + universales ───────────────────────
    result: Dict[str, Optional[str]] = {
        "Fund_Nature":    nature_canonical,   # ← Preservar nature detectada
        "Profile":        None,
        "Type":           None,
        "Strategy":       None,
        "Family":         None,
        "Style_Profile":  None,
        "Geography":      None,
        "Theme":          None,
        "Is_ESG":         0,
        "Exposure_Bias":  None,
        "Benchmark_Type": None,
        "Subtype":        None,
    }

    # Estructurado con caracterizacion minima
    if nature_raw == "Estructurado":
        result["Type"] = "Estructurado"
        if any(k in name_l for k in ["autocall", "autocallable"]):
            result["Subtype"]       = "Autocallable"
            result["Exposure_Bias"] = "Barrier Risk"
        elif any(k in name_l for k in ["capital protec", "guaranteed"]):
            result["Type"] = "Capital Protegido"
        result["Profile"] = "Moderado"

    elif any(k in name_l for k in ["fund of funds", "fof", "overlay"]):
        result["Type"] = "Fondo de Fondos"

    # Enriquecimiento desde KIID
    kiid_attrs = detect_kiid_attributes(kiid_text or "", nature_canonical, result)
    for k, v in kiid_attrs.items():
        if not result.get(k):
            result[k] = v

    # Atributos universales
    srri_val = srri_parsed                      # ← Usar srri_parsed
    if srri_val is None:
        import re
        m = re.search(r"\b([1-7])\s*/\s*7\b", text_l)
        srri_val = int(m.group(1)) if m else None

    if not result["Profile"]:
        result["Profile"]       = _detect_profile_from_srri(srri_val)
    result["Geography"]     = result["Geography"] or _detect_geography(name_l)
    result["Theme"]         = result["Theme"]     or _detect_theme(name_l) or "Core/General"
    result["Is_ESG"]        = max(result["Is_ESG"], _detect_is_esg(fund_name))
    if not result["Style_Profile"]:
        result["Style_Profile"] = _detect_style_profile(name_l)
    if not result["Exposure_Bias"]:
        result["Exposure_Bias"] = _detect_exposure_bias(name_l, nature_canonical)
    result["Strategy"]      = _detect_strategy(None, result.get("Subtype"), name_l)
    result["Benchmark_Type"] = _detect_benchmark_type(None, None)

    # ── Baja confianza: fallback conservador
    populated = sum(1 for k, v in result.items() if v is not None and k != "Is_ESG")
    if result["Fund_Nature"] == "Restantes" and populated <= 3:
        logger.warning(
            "[%s] Clasificación con baja confianza (%d atributos poblados). "
            "Aplicando valores conservadores.",
            result.get("ISIN", fund_name), populated,
        )
        result["Type"]   = None
        result["Family"] = None

    return apply_semantic_validation(result, fund_name)
```

---

## FICHERO 2: `pipeline.py`

### Cambio 1B — Pasar SRRI parseado a restantes

Localizar las líneas ~355-361 (la llamada a `classifier`):

```python
# ANTES:
            if classifier:
                # restantes.py acepta benchmark_declared como Capa 3 opcional
                _bench = parsed.get("Benchmark_Declared")
                try:
                    classification = classifier(fund_name, kiid_text,
                                                benchmark_declared=_bench)
                except TypeError:
                    classification = classifier(fund_name, kiid_text)
```

```python
# DESPUÉS:
            if classifier:
                # restantes.py acepta benchmark_declared y srri_parsed
                _bench = parsed.get("Benchmark_Declared")
                _srri_for_classify = parsed.get("SRRI")
                try:
                    classification = classifier(fund_name, kiid_text,
                                                benchmark_declared=_bench,
                                                srri_parsed=int(_srri_for_classify) if _srri_for_classify else None)
                except TypeError:
                    classification = classifier(fund_name, kiid_text)
```

---

## FICHERO 3: `classify_utils.py`

### Cambio 1C — Ampliar NAME_SIGNALS (nuevos patrones para fondos residuales)

**En NAME_SIGNALS_RV** (tras la última línea de la lista, antes del `]`), añadir:

```python
    # ── Fase 1C: patrones para 25 residuales ────────────────────────────
    "stk",                               # Vanguard: VANG PAC EXJAP STK
    "stk indx",                          # Vanguard: VGD US 500 STK INDX
    "us 500 st index",                   # Vanguard: VGD US 500 ST INDEX
    "pac exjap",                         # Vanguard: VANG PAC EXJAP
    "ashare",                            # JPM CHINA ASHARE OPP
    "china a-share",                     # variante
    "genetic therap",                    # JPM GENETIC THERAP
    "eur strat grow",                    # JPMORGAN EUR STRAT GROWT
    "strat grow",                        # variante corta
    "gbl div a",                         # FIDELITY GBL DIV A (equity dividend)
    "gl dividend",                       # FIDELITY GL DIVIDEND
    "glo divdnd",                        # FIDELITY GLO DIVDND
    "divdnd",                            # abreviación genérica dividendo
    "glbl infrastr",                     # DWS GLBL INFRASTR
    "glob infrastr",                     # variante
    "emergng mkts opp",                  # JPM EMERGNG MKTS OPP
    "emerg.mark.opport",                 # JPM EMERG.MARK.OPPORT (OCR con puntos)
    "por tc sol",                        # CARMIGNAC POR TC SOL
]
```

**En NAME_SIGNALS_RF_FLEXIBLE**, añadir (y corregir typo existente):

```python
    # ── Fase 1C: patrones para residuales + corrección typo ──────────────
    "r-co conv credi",                   # R-CO CONV CREDI EURO (fix typo: era "crdi")
    "us aggregate",                      # JPM US AGGREGATE BND
    "euro aggre",                        # JPMORGAN EURO AGGRE
    "srt dr bnd",                        # JPM GBL SRT DR BND (short duration bond)
    "glb bnd opp",                       # JPM GLB BND OPP (global bond opportunities)
    "meridi eur cred",                   # MFS MERIDI EUR CRED
    "em eu m ea",                        # FIDELITY EM EU M EA AF (EMEA multi-asset)
]
```

**NOTA:** También mover `"jpm gl bond opp"` de NAME_SIGNALS_MIXTO a NAME_SIGNALS_RF_FLEXIBLE, ya que JPM Global Bond Opportunities es un fondo de renta fija (SRRI=4, benchmark bond).

### Cambio 4A — Normalización Accumulation_Policy

En `validate_all_semantic_consistency()`, **antes** del bloque `# --- INTRA-ATRIBUTO` (línea ~2346), insertar:

```python
    # --- NORMALIZACIÓN INTRA-ATRIBUTO (Principio #8) ---

    # Accumulation_Policy: unificar casing
    _accum_norm = {"Accumulation": "ACCUMULATION", "Distribution": "DISTRIBUTION"}
    _accum = cr.get("Accumulation_Policy")
    if _accum in _accum_norm:
        cr["Accumulation_Policy"] = _accum_norm[_accum]

    # Currency_Hedged: mapear "Yes" → "Hedged"
    if cr.get("Currency_Hedged") == "Yes":
        cr["Currency_Hedged"] = "Hedged"
```

### Cambio 4C — Añadir "Tactical" a Style_Profile permitidos

En `ALLOWED_VALUES_BY_COLUMN["Style_Profile"]` (línea ~1879), añadir:

```python
    "Style_Profile": [
        "Growth", "Value", "Income", "Quality", "Momentum",
        "Low Volatility", "Risk Control", "Strategic Allocation",
        "Tactical",                                          # ← NUEVO
    ],
```

### Cambio 4C bis — Añadir "Long Only" a Exposure_Bias permitidos

El valor "Long Only" (929 fondos RV) no está en la lista. Añadir a `ALLOWED_VALUES_BY_COLUMN["Exposure_Bias"]`:

```python
    "Exposure_Bias": [
        "Duration Bias", "Credit Bias", "Liquidity Bias",
        "Income Bias", "Low Volatility Bias", "Absolute Return Bias",
        "Real Estate Bias", "Commodity Bias", "Barrier Risk",
        "Rate Reset Bias",
        "Long Only",                                         # ← NUEVO
    ],
```

### Cambio 4E — Añadir "Flexible Estratégico" para RF Flexible

En `ALLOWED_FAMILY_BY_NATURE["Renta Fija Flexible"]` (línea ~1933), añadir:

```python
    "Renta Fija Flexible": [
        "Renta Fija Flexible", "RF High Yield", "RF Emergentes",
        "RF Inflación", "Income Oriented",
        "Flexible Estratégico",                              # ← NUEVO
    ],
```

### Cambio 4E bis — Añadir "CNAV" para Monetario

"CNAV" (2 fondos) no está en la lista permitida para Monetario. Añadir:

```python
    "Monetario": [
        "Monetario", "LVNAV", "VNAV",
        "CNAV",                                              # ← NUEVO
    ],
```

### Cambio 4E ter — Añadir tipos faltantes en ALLOWED_TYPE_BY_NATURE

Hay 2 valores Type que genera RF Corto Plazo que no están permitidos:

```python
    "Renta Fija Corto Plazo": [
        "Renta Fija Corto Plazo", "Crédito CP", "Gobierno CP",
        "Floating Rate CP", "Target Maturity",
        "Deuda Pública CP",                                  # ← NUEVO
    ],
```

### Cambio 4D — Eliminar funciones duplicadas

Eliminar las **primeras** definiciones (líneas 789-878) de:
- `detect_nature_from_name` (línea 789)
- `_detect_kiid_format` (línea 837)
- `_get_obj_bounds` (línea 872)
- `_extract_window` (línea 878)

Las definiciones activas (segunda ocurrencia, líneas 883-976) se conservan intactas.

---

## VERIFICACIÓN POST-DESPLIEGUE

```bash
# 1. Limpiar caché
del /S /Q __pycache__
del /S /Q blocks\__pycache__
del /S /Q core\__pycache__

# 2. Ejecutar RESTANTES
python run_block.py --block restantes --db ../db/fondos.sqlite --master "c:\data\fondos\in\GestoresDeFondosv1.xlsx"

# 3. Verificar
sqlite3 ../db/fondos.sqlite "
  SELECT Fund_Nature, COUNT(*) cnt
  FROM fund_master WHERE Heuristic_Block='RESTANTES'
  GROUP BY Fund_Nature ORDER BY cnt DESC;
"
-- Esperado: Restantes ≤ 3

sqlite3 ../db/fondos.sqlite "
  SELECT Accumulation_Policy, COUNT(*)
  FROM fund_master WHERE Accumulation_Policy IS NOT NULL
  GROUP BY Accumulation_Policy;
"
-- Esperado: solo ACCUMULATION y DISTRIBUTION (sin Title Case)

sqlite3 ../db/fondos.sqlite "
  SELECT Currency_Hedged, COUNT(*)
  FROM fund_master WHERE Currency_Hedged IS NOT NULL
  GROUP BY Currency_Hedged;
"
-- Esperado: solo Hedged y Unhedged (sin 'Yes')
```
