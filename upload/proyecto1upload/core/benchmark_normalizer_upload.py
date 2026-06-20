# proyecto1/core/benchmark_normalizer.py
# -*- coding: utf-8 -*-
"""
Normalizador de benchmarks de fondos de inversion.

Transforma los textos de Benchmark_Declared (extraidos del KIID por el parser)
en identificadores canonicos estables, eliminando la fragmentacion de nombres.

Problema observado (3.204 fondos, analisis marzo 2026):
  - 165 variantes unicas para ~75 benchmarks reales
  - Ejemplo: "msci world index", "msci world nr", "msci world (net return",
    "msci world net index", "msci world www" → todos son MSCI_WORLD_NR
  - ~35 entradas con texto narrativo capturado (falsos positivos del parser)
  - ~19 entradas truncadas sin datos suficientes para normalizar

BL-39 (2026-04-13):
  - _FALSE_POSITIVE_FRAGMENTS: +9 fragmentos detectados en datos reales
    ("además", "uno o más tipos", "página 1 de", "canal", "último informe"...)
  - _CANONICAL_DATA: +20 aliases nuevos:
      MSCI: acwi-nr, ac worl (OCR), north america, usa high dividend,
            value index, india imi, india index-nr, emu index, frontier emerging
      Bloomberg: barclays euroagg corporate, barclays euro agg,
                 global aggregate 1-3, us high yield blend
      FTSE: all-share, ftse 100
      ICE BofA: euro high yield, bofaml 0-1y euro, bofaml euro corporate
      SOFR con paréntesis truncado "sofr)"

Uso:
    from core.benchmark_normalizer import normalize_benchmark, clean_benchmark

    raw = "bloomberg global aggregate index (eur hedged)) durante un ciclo"
    result = normalize_benchmark(raw)
    if result:
        print(result.canonical_id)    # BBG_GLOBAL_AGG
        print(result.canonical_name)  # Bloomberg Global Aggregate
        print(result.provider)        # Bloomberg
        print(result.asset_class)     # Fixed Income
        print(result.confidence)      # HIGH

Mantenimiento:
    Cuando aparezcan nuevas variantes no reconocidas, añadir a ALIAS_MAP.
    Los canonical_id son estables — no cambiar una vez asignados.
"""

import re
from dataclasses import dataclass
from typing import Optional


# ============================================================
# Resultado de normalizacion
# ============================================================

@dataclass
class BenchmarkResult:
    canonical_id:   str    # ej. MSCI_ACWI_NR
    canonical_name: str    # ej. MSCI ACWI (Net Return)
    provider:       str    # ej. MSCI
    asset_class:    str    # Equity / Fixed Income / Commodity / Rate / Mixed
    confidence:     str    # HIGH / MEDIUM / LOW


# ============================================================
# Patrones de FALSO POSITIVO
# ============================================================
# Si el texto contiene alguna de estas cadenas, NO es un benchmark:
# es texto narrativo capturado por error en el extractor.

_FALSE_POSITIVE_FRAGMENTS = [
    "acciones de un subfondo",
    "inversor minorista al",
    "este fondo es de capital variable",
    "aplica un filtro al índice",
    "aplica un filtro al indice",
    "cación de límites",
    "indicador de referencia,",    # coma al final → es texto, no nombre
    "adhieran a los criterios",
    "características medioam",
    "acumula ingresos",            # "esta clase de acciones acumula ingresos"
    "organismo de inversión colectiva en valores",
    "un organismo i",
    "fondos - global research",    # "jpmorgan funds - global research"
    "fondos de inversion",
    # BL-39: nuevos falsos positivos detectados en datos reales
    "además",                      # "sofr), además de..."
    "uno o más tipos",             # "msci acwi-nr y 40% jp morgan global uno o más tipos..."
    "page 1 de",                   # benchmark contaminado con número de página
    "página 1 de",
    "14 agosto",                   # "bloomberg euro-aggregate: página 1... 14 agosto de 2025"
    "canal",                       # "msci europe través de todos los canales"
    "último informe",              # "msci european último informe"
    "net de la",                   # texto narrativo con "net"
    # NOTE: "con dividendos netos" RETIRADO de falsos positivos — es un sufijo
    # Net-Return válido (p.ej. "S&P 500 Index (con dividendos netos)"). El
    # startswith() del matcher ignora el sufijo tras el prefijo canónico.
]

_FALSE_POSITIVE_RE = re.compile(
    "|".join(re.escape(f) for f in _FALSE_POSITIVE_FRAGMENTS),
    re.IGNORECASE
)


# ============================================================
# ============================================================
# Limpieza previa al matching
# ============================================================

# Contaminacion larga: texto narrativo arrastrado tras el nombre del índice.
# Solo elimina secuencias largas e inequívocamente no-benchmark.
# NO incluye palabras cortas como "eur","net","tr","index" porque aparecen
# en el interior de nombres válidos: "bloomberg EURO aggregate", "dax TR index".
_CONTAMINATION_RE = re.compile(
    r'\s+(?:durante\s+un\s+ciclo|un\s+organismo\s+[io]|'
    r'adhieran\s+a\s+los\s+criterios|caracter.sticas\s+medioam|'
    r'acumula\s+ingresos|fondos\s+de\s+inversion|depositario\s+eur|'
    r'introduciendo\s+determinados|y\s+promover\s+criterios|'
    r'calculada\s+como|plazo\s+personalizada|este\s+fondo\s+es\s+de|'
    r'una\s+mezcla|inversor\s+minorista|denominaci).+$',
    re.IGNORECASE | re.DOTALL
)

# Sufijos opcionales al final del nombre del índice.
# Se aplican SOLO en normalize_benchmark (stripping iterativo), NUNCA en clean_benchmark,
# para no eliminar texto válido que forma parte del nombre del benchmark.
_OPTIONAL_SUFFIX_RE = re.compile(
    r'(?:'
    r'\s*[\(\[]\s*(?:net\s+return|total\s+return|nr|tr|eur|usd|gbp|chf|'
    r'net\s+div[^)]{0,30}|net\s+tr|hedged\s+into[^)]{0,20}|'
    r'eur\s+hedged|usd\s+hedged|gbp\s+hedged|acwi)[^)\]]*[\)\]]|'
    r'\s+(?:index|índice|total\s+return|net\s+return)'
    r')\s*$',
    re.IGNORECASE
)


def clean_benchmark(raw: str) -> Optional[str]:
    """
    Limpia el texto bruto extraido del KIID.
    - Rechaza falsos positivos (texto narrativo confirmado).
    - Elimina contaminacion larga al final del nombre.
    - NO elimina palabras válidas de índices (eur, net, tr, index...).

    Devuelve None si es falso positivo o texto demasiado corto (<8 chars).
    """
    if not raw or not isinstance(raw, str):
        return None

    text = raw.strip().lower()
    text = re.sub(r'\s+', ' ', text)

    # 1. Rechazar falsos positivos (texto narrativo)
    if _FALSE_POSITIVE_RE.search(text):
        return None

    # 2. Eliminar contaminación narrativa larga al final
    text = _CONTAMINATION_RE.sub('', text).strip()

    # 3. Limpiar puntuación suelta al final
    text = re.sub(r'\s*[,\.;]\s*$', '', text).strip()
    text = re.sub(r'\s*\)\s*$', '', text).strip()
    text = re.sub(r'\s*\(\s*$', '', text).strip()  # "(" suelto al final

    # 4. Demasiado corto para ser útil — umbral reducido para €STR, SOFR, etc.
    if len(text) < 4:
        return None

    return text


# ============================================================
# Mapa canonico: alias → BenchmarkResult
# ============================================================
# Orden importa: mas especifico ANTES que mas general.
# El matching usa startswith() sobre el texto limpiado por clean_benchmark().

_CANONICAL_DATA: list[tuple[str, BenchmarkResult]] = [

    # ── MSCI Renta Variable ───────────────────────────────────────────────────
    ("msci ac world (acwi)",        BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci acwi index",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci acwi net",               BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci all countr",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world nr",            BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world net",           BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world total",         BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (nr)",          BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (net",          BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world index",         BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci world index",            BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","HIGH")),
    ("msci world nr",               BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","HIGH")),
    ("msci world net",              BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","HIGH")),
    ("msci world (net",             BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","HIGH")),
    ("msci world www",              BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci europe index",           BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci europe nr",              BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci europe net",             BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci emerging markets index", BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","HIGH")),
    ("msci emerging markets net",   BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","HIGH")),
    ("msci emerging markets nr",    BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","HIGH")),
    ("msci emerging markets",       BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci ac asia ex japan index", BenchmarkResult("MSCI_ASIA_EX_JP","MSCI AC Asia ex Japan","MSCI","Equity","HIGH")),
    ("msci ac asia ex japan",       BenchmarkResult("MSCI_ASIA_EX_JP","MSCI AC Asia ex Japan","MSCI","Equity","HIGH")),
    ("msci ac asia ex-japan 10/40", BenchmarkResult("MSCI_ASIA_EX_JP_1040","MSCI AC Asia ex Japan 10/40","MSCI","Equity","HIGH")),
    ("msci ac asia ex-japan small", BenchmarkResult("MSCI_ASIA_EX_JP_SC","MSCI AC Asia ex Japan Small Cap","MSCI","Equity","HIGH")),
    ("msci ac asia www",            BenchmarkResult("MSCI_ASIA_EX_JP","MSCI AC Asia ex Japan","MSCI","Equity","MEDIUM")),
    ("msci ac asia pacific ex japan (net", BenchmarkResult("MSCI_APAC_EX_JP","MSCI AC Asia Pacific ex Japan","MSCI","Equity","HIGH")),
    ("msci ac asia pacific ex japan index", BenchmarkResult("MSCI_APAC_EX_JP","MSCI AC Asia Pacific ex Japan","MSCI","Equity","HIGH")),
    ("msci ac asia pacific ex japan small", BenchmarkResult("MSCI_APAC_EX_JP_SC","MSCI AC Asia Pacific ex Japan Small Cap","MSCI","Equity","HIGH")),
    ("msci ac asia pacific",        BenchmarkResult("MSCI_APAC","MSCI AC Asia Pacific","MSCI","Equity","HIGH")),
    ("msci ac pacific",             BenchmarkResult("MSCI_APAC","MSCI AC Asia Pacific","MSCI","Equity","MEDIUM")),
    ("msci ac world financials",    BenchmarkResult("MSCI_ACWI_FINANCIALS","MSCI ACWI Financials","MSCI","Equity","HIGH")),
    ("msci ac world information technology 10/40", BenchmarkResult("MSCI_ACWI_IT_1040","MSCI ACWI IT 10/40","MSCI","Equity","HIGH")),
    ("msci ac world information technology", BenchmarkResult("MSCI_ACWI_IT","MSCI ACWI Information Technology","MSCI","Equity","HIGH")),
    ("msci ac world health care",   BenchmarkResult("MSCI_ACWI_HC","MSCI ACWI Health Care","MSCI","Equity","HIGH")),
    ("msci ac world industrials",   BenchmarkResult("MSCI_ACWI_MIXED_SECTOR","MSCI ACWI Multi-Sector","MSCI","Equity","MEDIUM")),
    ("msci world information technology 10/40", BenchmarkResult("MSCI_WORLD_IT_1040","MSCI World IT 10/40","MSCI","Equity","HIGH")),
    ("msci world information technology", BenchmarkResult("MSCI_WORLD_IT","MSCI World Information Technology","MSCI","Equity","HIGH")),
    ("msci world health care",      BenchmarkResult("MSCI_WORLD_HC","MSCI World Health Care","MSCI","Equity","HIGH")),
    ("msci world small cap",        BenchmarkResult("MSCI_WORLD_SC","MSCI World Small Cap","MSCI","Equity","HIGH")),
    ("msci world value",            BenchmarkResult("MSCI_WORLD_VALUE","MSCI World Value","MSCI","Equity","HIGH")),

    # ── Bloomberg Renta Fija ──────────────────────────────────────────────────
    ("bloomberg global aggregate corporate 1-5", BenchmarkResult("BBG_GLOBAL_AGG_CORP_1_5","Bloomberg Global Agg Corp 1-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate corporate (con", BenchmarkResult("BBG_GLOBAL_AGG_CORP","Bloomberg Global Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate corporate",  BenchmarkResult("BBG_GLOBAL_AGG_CORP","Bloomberg Global Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate-credit",     BenchmarkResult("BBG_GLOBAL_AGG_CREDIT","Bloomberg Global Aggregate Credit","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg objetivos global aggregate-credit", BenchmarkResult("BBG_GLOBAL_AGG_CREDIT","Bloomberg Global Aggregate Credit","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg global aggregate 1-5",        BenchmarkResult("BBG_GLOBAL_AGG_1_5","Bloomberg Global Aggregate 1-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate 1-3",        BenchmarkResult("BBG_GLOBAL_AGG_1_3","Bloomberg Global Aggregate 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg agg 3-5",                     BenchmarkResult("BBG_GLOBAL_AGG_3_5","Bloomberg Global Aggregate 3-5Y","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg global aggregate usd hedged", BenchmarkResult("BBG_GLOBAL_AGG_USDH","Bloomberg Global Aggregate (USD Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global-aggregate index (hedged into eur)", BenchmarkResult("BBG_GLOBAL_AGG_EURH","Bloomberg Global Aggregate (EUR Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate index (eur hedged", BenchmarkResult("BBG_GLOBAL_AGG_EURH","Bloomberg Global Aggregate (EUR Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate bond",       BenchmarkResult("BBG_GLOBAL_AGG","Bloomberg Global Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggregate index",      BenchmarkResult("BBG_GLOBAL_AGG","Bloomberg Global Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global aggreg",               BenchmarkResult("BBG_GLOBAL_AGG","Bloomberg Global Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg globalagg",                   BenchmarkResult("BBG_GLOBAL_AGG","Bloomberg Global Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg euro aggregate (1-3 y)",      BenchmarkResult("BBG_EURO_AGG_1_3","Bloomberg Euro Aggregate 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate (1-3yr)",      BenchmarkResult("BBG_EURO_AGG_1_3","Bloomberg Euro Aggregate 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate 1-3",          BenchmarkResult("BBG_EURO_AGG_1_3","Bloomberg Euro Aggregate 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro-aggregate 500mm 1-3",    BenchmarkResult("BBG_EURO_AGG_500_1_3","Bloomberg Euro Aggregate 500M+ 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euroaggregate500mio+corporate", BenchmarkResult("BBG_EURO_AGG_500_CORP","Bloomberg Euro Aggregate 500M+ Corp","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euroaggregate500mio+",        BenchmarkResult("BBG_EURO_AGG_500","Bloomberg Euro Aggregate 500M+","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate 1-10",         BenchmarkResult("BBG_EURO_AGG_1_10","Bloomberg Euro Aggregate 1-10Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro-aggregate treasury aaa 1-10", BenchmarkResult("BBG_EURO_AGG_TREAS_1_10","Bloomberg Euro Agg Treasury AAA 1-10Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro-aggregate 10+",          BenchmarkResult("BBG_EURO_AGG_10P","Bloomberg Euro Aggregate 10+Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate corporates financials", BenchmarkResult("BBG_EURO_AGG_CORP_FIN_SUB","Bloomberg Euro Agg Corp Financials Sub 2%","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate corporate",    BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro-aggregate: corp",        BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate: corporates",  BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro corporate",              BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg capital euro aggregate corporate", BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg barclays euro aggregate corporate", BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro government float",       BenchmarkResult("BBG_EURO_GOVT","Bloomberg Euro Government","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg germany govt",                BenchmarkResult("BBG_GERMANY_GOVT","Bloomberg Germany Government","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg barclays euro government",    BenchmarkResult("BBG_EURO_GOVT","Bloomberg Euro Government","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro treasury 50bn",          BenchmarkResult("BBG_EURO_TREAS_50BN","Bloomberg Euro Treasury 50bn+","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate treasury 3-5", BenchmarkResult("BBG_EURO_TREAS_3_5","Bloomberg Euro Aggregate Treasury 3-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate bond",         BenchmarkResult("BBG_EURO_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate index",        BenchmarkResult("BBG_EURO_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg euro aggregate) y",           BenchmarkResult("BBG_EURO_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg euro aggregate",              BenchmarkResult("BBG_EURO_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us corporate high yield",     BenchmarkResult("BBG_US_HY","Bloomberg US Corporate High Yield","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us high yield",               BenchmarkResult("BBG_US_HY","Bloomberg US Corporate High Yield","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us corporate",                BenchmarkResult("BBG_US_CORP","Bloomberg US Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us aggregate",                BenchmarkResult("BBG_US_AGG","Bloomberg US Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us universal",                BenchmarkResult("BBG_US_UNIVERSAL","Bloomberg US Universal","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global high yield corporate index (hedged into usd)", BenchmarkResult("BBG_GLOBAL_HY_USDH","Bloomberg Global HY Corporate (USD Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global high yield corporate index (hedged into eur)", BenchmarkResult("BBG_GLOBAL_HY_EURH","Bloomberg Global HY Corporate (EUR Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global high yield corporate", BenchmarkResult("BBG_GLOBAL_HY_CORP","Bloomberg Global HY Corporate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global high yield",           BenchmarkResult("BBG_GLOBAL_HY","Bloomberg Global High Yield","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg eurodollar corporate aaa-bbb 1-5", BenchmarkResult("BBG_EURODOLLAR_CORP_1_5","Bloomberg Eurodollar Corp AAA-BBB 1-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg multiverse usd hedged",       BenchmarkResult("BBG_MULTIVERSE_USDH","Bloomberg Multiverse (USD Hedged)","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg multiverse",                  BenchmarkResult("BBG_MULTIVERSE","Bloomberg Multiverse","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg china aggregate",             BenchmarkResult("BBG_CHINA_AGG","Bloomberg China Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg global inflation linked",     BenchmarkResult("BBG_GLOBAL_INFL","Bloomberg Global Inflation Linked","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg world government",            BenchmarkResult("BBG_WORLD_GOVT","Bloomberg World Government Bond","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg commodity",                   BenchmarkResult("BBG_COMMODITY","Bloomberg Commodity","Bloomberg","Commodity","HIGH")),
    ("bloomberg msci green bond 10",          BenchmarkResult("BBG_MSCI_GREEN_10PCT","Bloomberg MSCI Green Bond 10% Capped","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci green bond",             BenchmarkResult("BBG_MSCI_GREEN","Bloomberg MSCI Green Bond","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci euro corporate sri pab", BenchmarkResult("BBG_MSCI_EURO_CORP_SRI_PAB","Bloomberg MSCI Euro Corp SRI PAB","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci 3-5 year euro corporate sri", BenchmarkResult("BBG_MSCI_EURO_CORP_SRI_3_5","Bloomberg MSCI Euro Corp SRI 3-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci 1-3 year euro corporate sri", BenchmarkResult("BBG_MSCI_EURO_CORP_SRI_1_3","Bloomberg MSCI Euro Corp SRI 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci esg euro corporate 1-5", BenchmarkResult("BBG_MSCI_EURO_CORP_ESG_1_5","Bloomberg MSCI Euro Corp ESG 1-5Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci euro corporate sri sustainable", BenchmarkResult("BBG_MSCI_EURO_CORP_SRI","Bloomberg MSCI Euro Corp SRI","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg msci euro corporate sustainable sri", BenchmarkResult("BBG_MSCI_EURO_CORP_SRI","Bloomberg MSCI Euro Corp SRI","Bloomberg","Fixed Income","HIGH")),

    # ── ICE BofA Renta Fija ───────────────────────────────────────────────────
    ("ice bofa sofr overnight",               BenchmarkResult("ICE_SOFR_ON","ICE BofA SOFR Overnight Rate","ICE BofA","Rate","HIGH")),
    ("ice bofa 3-month german treasury bill", BenchmarkResult("ICE_EURO_TBILL_3M","ICE BofA 3M German Treasury Bill","ICE BofA","Rate","HIGH")),
    ("ice bofa 3 month us treasury bill",     BenchmarkResult("ICE_US_TBILL_3M","ICE BofA 3M US Treasury Bill","ICE BofA","Rate","HIGH")),
    ("ice bofa us 3-month treasury bill",     BenchmarkResult("ICE_US_TBILL_3M","ICE BofA 3M US Treasury Bill","ICE BofA","Rate","HIGH")),
    ("ice bofaml 0-1 years euro government",  BenchmarkResult("ICE_EURO_GOVT_0_1","ICE BofA 0-1Y Euro Government","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa 1-3 year all euro government", BenchmarkResult("ICE_EURO_GOVT_1_3","ICE BofA 1-3Y Euro Government","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa emu",                          BenchmarkResult("ICE_EMU_GOVT","ICE BofA EMU Government","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa euro high yield ex financial bb-b 1-3", BenchmarkResult("ICE_EURO_HY_EX_FIN_BB_B_1_3","ICE BofA Euro HY ex-Fin BB-B 1-3Y","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa euro high yield bb-b constrained", BenchmarkResult("ICE_EURO_HY_BB_B_CONST","ICE BofA Euro HY BB-B Constrained","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa bb-b euro non-financial high yield constrained", BenchmarkResult("ICE_EURO_HY_BB_B_CONST","ICE BofA Euro HY BB-B Constrained","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa european currency non-financial high yield constrained", BenchmarkResult("ICE_EUR_NONFIN_HY_CONST","ICE BofA Euro Non-Fin HY Constrained","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa euro high yield constrained",  BenchmarkResult("ICE_EURO_HY_CONST","ICE BofA Euro HY Constrained","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa euro high yield",              BenchmarkResult("ICE_EURO_HY","ICE BofA Euro High Yield","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa 1-5 year a-bbb euro corporate", BenchmarkResult("ICE_EURO_CORP_A_BBB_1_5","ICE BofA A-BBB Euro Corp 1-5Y","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml a-bbb euro corporate",       BenchmarkResult("ICE_EURO_CORP_A_BBB","ICE BofA A-BBB Euro Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa 1-3year euro corporate",       BenchmarkResult("ICE_EURO_CORP_1_3","ICE BofA Euro Corporate 1-3Y","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml 1-3 year",                   BenchmarkResult("ICE_EURO_CORP_1_3","ICE BofA Euro Corporate 1-3Y","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa 1- 3 year euro broad market",  BenchmarkResult("ICE_EURO_BROAD_1_3","ICE BofA Euro Broad Market 1-3Y","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa 1-3 year euro broad market",   BenchmarkResult("ICE_EURO_BROAD_1_3","ICE BofA Euro Broad Market 1-3Y","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa merrill lynch euro corporate", BenchmarkResult("ICE_EURO_CORP","ICE BofA Euro Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml euro corporate",             BenchmarkResult("ICE_EURO_CORP","ICE BofA Euro Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa euro corporate",               BenchmarkResult("ICE_EURO_CORP","ICE BofA Euro Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bank of america merrill lynch us high yield constrained", BenchmarkResult("ICE_US_HY_CONST","ICE BofA US HY Constrained","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml bb-b rated developed markets high yield", BenchmarkResult("ICE_DM_HY_BB_B","ICE BofA BB-B DM HY","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml global high yield constrained usd hedged", BenchmarkResult("ICE_GLOBAL_HY_CONST_USDH","ICE BofA Global HY Constrained (USD Hedged)","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml global floating rate high yield", BenchmarkResult("ICE_GLOBAL_FRN_HY","ICE BofA Global Floating Rate HY","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa global high yield european issuers constrained", BenchmarkResult("ICE_GLOBAL_HY_EUR_CONST","ICE BofA Global HY European Issuers Const.","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa global high yield",            BenchmarkResult("ICE_GLOBAL_HY","ICE BofA Global High Yield","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa ml global hybrid non-financial corporate", BenchmarkResult("ICE_GLOBAL_HYBRID_NONFIN","ICE BofA Global Hybrid Non-Financial Corp","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa global corporate",             BenchmarkResult("ICE_GLOBAL_CORP","ICE BofA Global Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa sterling corporate",           BenchmarkResult("ICE_GBP_CORP","ICE BofA Sterling Corporate","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa asia dollar investment grade", BenchmarkResult("ICE_ASIA_USD_IG","ICE BofA Asia Dollar Investment Grade","ICE BofA","Fixed Income","HIGH")),
    ("ice bofa aa-bbb abs",                   BenchmarkResult("ICE_ABS_AA_BBB","ICE BofA AA-BBB ABS","ICE BofA","Fixed Income","HIGH")),
    ("ice libor eur",                         BenchmarkResult("ICE_LIBOR_EUR","ICE LIBOR EUR","ICE BofA","Rate","MEDIUM")),
    ("barclays overnight eur",                BenchmarkResult("BBG_OVERNIGHT_EUR","Bloomberg Euro Overnight (ex-Barclays)","Bloomberg","Rate","HIGH")),
    ("barclays euroagg corporate",            BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("barclays euro-aggregate corporate",     BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),

    # ── FTSE ─────────────────────────────────────────────────────────────────
    ("ftse all-share",                        BenchmarkResult("FTSE_ALL_SHARE","FTSE All-Share","FTSE","Equity","HIGH")),
    ("ftse all share",                        BenchmarkResult("FTSE_ALL_SHARE","FTSE All-Share","FTSE","Equity","HIGH")),
    ("ftse all- share",                       BenchmarkResult("FTSE_ALL_SHARE","FTSE All-Share","FTSE","Equity","MEDIUM")),
    ("ftse world government bond",            BenchmarkResult("FTSE_WORLD_GOVT","FTSE World Government Bond","FTSE","Fixed Income","HIGH")),
    ("ftse world europe ex uk",               BenchmarkResult("FTSE_WORLD_EU_EX_UK","FTSE World Europe ex UK","FTSE","Equity","HIGH")),
    ("ftse gold mines",                       BenchmarkResult("FTSE_GOLD_MINES","FTSE Gold Mines","FTSE","Equity","HIGH")),
    ("ftse epra/nareit developed",            BenchmarkResult("FTSE_EPRA_NAREIT_DEV","FTSE EPRA/NAREIT Developed","FTSE","Equity","HIGH")),
    ("ftse epra nareit developed",            BenchmarkResult("FTSE_EPRA_NAREIT_DEV","FTSE EPRA/NAREIT Developed","FTSE","Equity","HIGH")),
    ("ftse epra",                             BenchmarkResult("FTSE_EPRA_NAREIT_DEV","FTSE EPRA/NAREIT Developed","FTSE","Equity","MEDIUM")),
    ("ftse global developed core infrastructure 50/50", BenchmarkResult("FTSE_INFRA_5050","FTSE Global Core Infrastructure 50/50","FTSE","Equity","HIGH")),
    ("ftse global core infrastructure 50/50", BenchmarkResult("FTSE_INFRA_5050","FTSE Global Core Infrastructure 50/50","FTSE","Equity","HIGH")),
    ("ftse global focus convertible",         BenchmarkResult("FTSE_GLOBAL_CONV","FTSE Global Focus Convertible","FTSE","Mixed","HIGH")),
    ("ftse global convertible",               BenchmarkResult("FTSE_GLOBAL_CONV","FTSE Global Convertible","FTSE","Mixed","HIGH")),
    ("ftse eurozone convertible",             BenchmarkResult("FTSE_EU_CONV","FTSE Eurozone Convertible Bond","FTSE","Mixed","HIGH")),
    ("ftse emerging all cap",                 BenchmarkResult("FTSE_EM_ALL_CAP","FTSE Emerging All Cap","FTSE","Equity","MEDIUM")),
    ("ftse italia all share",                 BenchmarkResult("FTSE_ITALIA_ALL","FTSE Italia All Share","FTSE","Equity","HIGH")),
    ("ftse italia all-",                      BenchmarkResult("FTSE_ITALIA_ALL","FTSE Italia All Share","FTSE","Equity","MEDIUM")),
    ("ftse emu government",                   BenchmarkResult("FTSE_EMU_GOVT","FTSE EMU Government Bond","FTSE","Fixed Income","HIGH")),
    ("ftse euro government",                  BenchmarkResult("FTSE_EURO_GOVT","FTSE Euro Government Bond","FTSE","Fixed Income","HIGH")),
    ("ftse nordic capped",                    BenchmarkResult("FTSE_NORDIC_10PCT","FTSE Nordic Capped 10%","FTSE","Equity","HIGH")),

    # ── JP Morgan ─────────────────────────────────────────────────────────────
    ("jp morgan embi global diversified custom", BenchmarkResult("JPM_EMBI_GD_CUSTOM","JP Morgan EMBI Global Diversified Custom","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan embi global diversified",     BenchmarkResult("JPM_EMBI_GD","JP Morgan EMBI Global Diversified","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan embi global index",           BenchmarkResult("JPM_EMBI_G","JP Morgan EMBI Global","JP Morgan","Fixed Income","HIGH")),
    ("jpmorgan emerging markets bond",        BenchmarkResult("JPM_EMBI_G","JP Morgan EMBI Global","JP Morgan","Fixed Income","MEDIUM")),
    ("jp morgan gbi-em global diversified",   BenchmarkResult("JPM_GBI_EM_GD","JP Morgan GBI-EM Global Diversified","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan global government bond (hedged", BenchmarkResult("JPM_GLOBAL_GOVT_EURH","JP Morgan Global Govt Bond (EUR Hedged)","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan global government bond",      BenchmarkResult("JPM_GLOBAL_GOVT","JP Morgan Global Government Bond","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan asian credit non-investment grade", BenchmarkResult("JPM_ASIA_CREDIT_HY","JP Morgan Asian Credit Non-IG","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan asian credit non- investment grade", BenchmarkResult("JPM_ASIA_CREDIT_HY","JP Morgan Asian Credit Non-IG","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan asia credit",                 BenchmarkResult("JPM_ASIA_CREDIT","JP Morgan Asia Credit","JP Morgan","Fixed Income","HIGH")),

    # ── MSCI — variantes adicionales ─────────────────────────────────────────
    # BL-39: aliases detectados en análisis de datos reales (p1_export_20260413)
    ("msci acwi-nr",                    BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac worl",                    BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","MEDIUM")),  # OCR truncado
    ("msci north america",              BenchmarkResult("MSCI_NORTH_AMERICA","MSCI North America","MSCI","Equity","HIGH")),
    ("msci usa high dividend yield",    BenchmarkResult("MSCI_USA_HIGH_DIV","MSCI USA High Dividend Yield","MSCI","Equity","HIGH")),
    ("msci usa high dividend",          BenchmarkResult("MSCI_USA_HIGH_DIV","MSCI USA High Dividend Yield","MSCI","Equity","MEDIUM")),
    ("msci value index",                BenchmarkResult("MSCI_WORLD_VALUE","MSCI World Value","MSCI","Equity","MEDIUM")),
    ("msci india imi",                  BenchmarkResult("MSCI_INDIA_IMI","MSCI India IMI","MSCI","Equity","HIGH")),
    ("msci india index",                BenchmarkResult("MSCI_INDIA","MSCI India","MSCI","Equity","HIGH")),
    ("msci india index-nr",             BenchmarkResult("MSCI_INDIA","MSCI India","MSCI","Equity","HIGH")),
    ("msci emu index",                  BenchmarkResult("MSCI_EMU_NR","MSCI EMU (Net Return)","MSCI","Equity","HIGH")),
    ("msci frontier emerging",          BenchmarkResult("MSCI_FRONTIER_EM","MSCI Frontier Emerging Markets","MSCI","Equity","HIGH")),
    ("msci ac world (usd",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (eur",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (nr",              BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (usd)",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (eur)",             BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac world (nr)",              BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci world growth",               BenchmarkResult("MSCI_WORLD_GROWTH","MSCI World Growth","MSCI","Equity","HIGH")),
    ("msci world value",                BenchmarkResult("MSCI_WORLD_VALUE","MSCI World Value","MSCI","Equity","HIGH")),
    ("msci world",                      BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","HIGH")),
    ("msci daily net total return world", BenchmarkResult("MSCI_WORLD_NR","MSCI World (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci europe (net tr",             BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci europe (net return",         BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci europe (net return)",        BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","HIGH")),
    ("msci europe convertido",          BenchmarkResult("MSCI_EUROPE_NR","MSCI Europe (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci europe small cap",           BenchmarkResult("MSCI_EUROPE_SC","MSCI Europe Small Cap","MSCI","Equity","HIGH")),
    ("msci emu",                        BenchmarkResult("MSCI_EMU_NR","MSCI EMU (Net Return)","MSCI","Equity","HIGH")),
    ("msci em nr",                      BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","HIGH")),
    ("msci ac asia",                    BenchmarkResult("MSCI_ASIA_EX_JP","MSCI AC Asia ex Japan","MSCI","Equity","MEDIUM")),
    ("msci china a onshore",            BenchmarkResult("MSCI_CHINA_A","MSCI China A Onshore","MSCI","Equity","HIGH")),
    ("msci china",                      BenchmarkResult("MSCI_CHINA","MSCI China","MSCI","Equity","HIGH")),
    ("msci india",                      BenchmarkResult("MSCI_INDIA","MSCI India","MSCI","Equity","HIGH")),
    ("msci japan",                      BenchmarkResult("MSCI_JAPAN_NR","MSCI Japan (Net Return)","MSCI","Equity","HIGH")),
    ("msci frontier emerging markets",  BenchmarkResult("MSCI_FRONTIER_EM","MSCI Frontier Emerging Markets","MSCI","Equity","HIGH")),
    ("msci frontier",                   BenchmarkResult("MSCI_FRONTIER","MSCI Frontier Markets","MSCI","Equity","HIGH")),
    ("msci acwi (net return",           BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci acwi (net ret",              BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","HIGH")),
    ("msci acwi financials",            BenchmarkResult("MSCI_ACWI_FINANCIALS","MSCI ACWI Financials","MSCI","Equity","HIGH")),
    ("msci acwi",                       BenchmarkResult("MSCI_ACWI_NR","MSCI ACWI (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci acwi climate change",        BenchmarkResult("MSCI_ACWI_CLIMATE","MSCI ACWI Climate Change","MSCI","Equity","HIGH")),
    ("msci acwi information technology",BenchmarkResult("MSCI_ACWI_IT","MSCI ACWI Information Technology","MSCI","Equity","HIGH")),
    ("msci golden dragon",              BenchmarkResult("MSCI_CHINA_GOLDEN","MSCI Golden Dragon 10/40","MSCI","Equity","HIGH")),
    ("msci em",                         BenchmarkResult("MSCI_EM_NR","MSCI Emerging Markets (Net Return)","MSCI","Equity","MEDIUM")),
    ("msci value",                      BenchmarkResult("MSCI_WORLD_VALUE","MSCI World Value","MSCI","Equity","MEDIUM")),

    # ── S&P ───────────────────────────────────────────────────────────────────
    ("s&p 500 (36 %)",                  BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","MEDIUM")),
    ("s&p 500 index (con dividendos",   BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","HIGH")),
    ("s&p 500 composite index",         BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","HIGH")),
    ("s&p500 index",                    BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","HIGH")),
    ("s&p 500 index",                   BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","HIGH")),
    ("s&p 500screened",                 BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","MEDIUM")),
    ("s&p 500",                         BenchmarkResult("SP500_NR","S&P 500 (Net Return)","S&P","Equity","HIGH")),
    ("s&p europe bmi",                  BenchmarkResult("SP_EUROPE_BMI","S&P Europe BMI","S&P","Equity","HIGH")),
    ("s&p global luxury",               BenchmarkResult("SP_GLOBAL_LUXURY","S&P Global Luxury","S&P","Equity","HIGH")),
    ("s&p europe",                      BenchmarkResult("SP_EUROPE","S&P Europe 350","S&P","Equity","MEDIUM")),

    # ── Russell ───────────────────────────────────────────────────────────────
    ("russell 3000 growth",             BenchmarkResult("RUSSELL_3000_GROWTH","Russell 3000 Growth","Russell","Equity","HIGH")),
    ("russell 3000",                    BenchmarkResult("RUSSELL_3000","Russell 3000","Russell","Equity","HIGH")),
    ("russell 2500",                    BenchmarkResult("RUSSELL_2500","Russell 2500","Russell","Equity","HIGH")),
    ("russell 1000 growth",             BenchmarkResult("RUSSELL_1000_GROWTH","Russell 1000 Growth","Russell","Equity","HIGH")),
    ("russell 1000",                    BenchmarkResult("RUSSELL_1000","Russell 1000","Russell","Equity","HIGH")),
    ("russell 2000",                    BenchmarkResult("RUSSELL_2000","Russell 2000","Russell","Equity","HIGH")),

    # ── Bloomberg variantes adicionales ───────────────────────────────────────
    # BL-39: aliases detectados en datos reales
    ("barclays euroagg corporate",      BenchmarkResult("BBG_EURO_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","HIGH")),
    ("barclays euro agg",               BenchmarkResult("BBG_EURO_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg global aggregate 1-3",  BenchmarkResult("BBG_GLOBAL_AGG_1_3","Bloomberg Global Aggregate 1-3Y","Bloomberg","Fixed Income","HIGH")),
    ("bloomberg us high yield, 30",     BenchmarkResult("BBG_US_HY_30_EUR_HY","Bloomberg US HY / Pan-European HY blend","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg global",                BenchmarkResult("BBG_GLOBAL_AGG","Bloomberg Global Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg u",                     BenchmarkResult("BBG_US_AGG","Bloomberg US Aggregate","Bloomberg","Fixed Income","MEDIUM")),

    # ── TOPIX / NIKKEI ────────────────────────────────────────────────────────
    ("topix net return",                BenchmarkResult("TOPIX_NR","TOPIX (Net Return)","Tokyo Stock Exchange","Equity","HIGH")),
    ("topix total return",              BenchmarkResult("TOPIX_NR","TOPIX (Net Return)","Tokyo Stock Exchange","Equity","HIGH")),
    ("topix",                           BenchmarkResult("TOPIX","TOPIX","Tokyo Stock Exchange","Equity","HIGH")),
    ("nikkei 225",                      BenchmarkResult("NIKKEI_225","Nikkei 225","Nikkei","Equity","HIGH")),

    # ── JP Morgan adicionales ─────────────────────────────────────────────────
    ("jp morgan us government bond",    BenchmarkResult("JPM_US_GOVT","JP Morgan US Government Bond","JP Morgan","Fixed Income","HIGH")),
    ("jp morgan government bond index", BenchmarkResult("JPM_GLOBAL_GOVT","JP Morgan Global Government Bond","JP Morgan","Fixed Income","HIGH")),

    # ── FTSE adicionales ─────────────────────────────────────────────────────
    # BL-39: detectados en datos reales
    ("ftse all-share",                  BenchmarkResult("FTSE_ALL_SHARE","FTSE All-Share","FTSE","Equity","HIGH")),
    ("ftse all share",                  BenchmarkResult("FTSE_ALL_SHARE","FTSE All-Share","FTSE","Equity","HIGH")),
    ("ftse 100",                        BenchmarkResult("FTSE_100","FTSE 100","FTSE","Equity","HIGH")),

    # ── ICE BofA adicionales ──────────────────────────────────────────────────
    # BL-39: detectados en datos reales
    ("ice bofa euro high yield",        BenchmarkResult("ICE_EUR_HY","ICE BofA Euro High Yield","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml 0-1 years euro",       BenchmarkResult("ICE_EUR_GOVT_0_1","ICE BofAML 0-1Y Euro Government","ICE BofA","Fixed Income","HIGH")),
    ("ice bofaml euro corporate",       BenchmarkResult("ICE_EUR_CORP","ICE BofAML Euro Corporate","ICE BofA","Fixed Income","HIGH")),

    # ── Tipos de interés adicionales ──────────────────────────────────────────
    # BL-39: "sofr)" con paréntesis truncado aparece en datos contaminados
    ("sofr)",                           BenchmarkResult("RATE_SOFR","Secured Overnight Financing Rate (SOFR)","FRB","Rate","MEDIUM")),
    ("€str",                            BenchmarkResult("RATE_ESTR","Euro Short-Term Rate (€STR)","ECB","Rate","HIGH")),
    ("sofr",                            BenchmarkResult("RATE_SOFR","Secured Overnight Financing Rate (SOFR)","FRB","Rate","HIGH")),

    # ── STOXX ─────────────────────────────────────────────────────────────────
    ("stoxx europe 600",                BenchmarkResult("STOXX_EU_600","STOXX Europe 600","STOXX","Equity","HIGH")),
    ("euro stoxx 50",                   BenchmarkResult("EURO_STOXX_50","Euro STOXX 50","STOXX","Equity","HIGH")),
    ("euro stoxx",                      BenchmarkResult("EURO_STOXX","Euro STOXX","STOXX","Equity","MEDIUM")),
    ("ftse eur 1-month eurodeposit",    BenchmarkResult("FTSE_EUR_1M_DEPOSIT","FTSE EUR 1M Eurodeposit","FTSE","Rate","HIGH")),
    ("ftse usd 1-month eurodeposit",    BenchmarkResult("FTSE_USD_1M_DEPOSIT","FTSE USD 1M Eurodeposit","FTSE","Rate","HIGH")),
    ("ftse usd",                        BenchmarkResult("FTSE_USD_1M_DEPOSIT","FTSE USD 1M Eurodeposit","FTSE","Rate","MEDIUM")),
    ("stoxx",                           BenchmarkResult("STOXX_EU_600","STOXX Europe 600","STOXX","Equity","MEDIUM")),


    # ── NASDAQ ───────────────────────────────────────────────────────────────
    ("nasdaq biotechnology",            BenchmarkResult("NASDAQ_BIOTECH","NASDAQ Biotechnology","NASDAQ","Equity","HIGH")),
    ("nasdaq composite",                BenchmarkResult("NASDAQ_COMPOSITE","NASDAQ Composite","NASDAQ","Equity","HIGH")),
    ("nasdaq 100",                      BenchmarkResult("NASDAQ_100","NASDAQ 100","NASDAQ","Equity","HIGH")),
    ("nasdaq",                          BenchmarkResult("NASDAQ_100","NASDAQ 100","NASDAQ","Equity","MEDIUM")),

    # ── iBoxx ─────────────────────────────────────────────────────────────────
    ("iboxx € corporates 1-3",               BenchmarkResult("IBOXX_EURO_CORP_1_3","iBoxx EUR Corporates 1-3Y","iBoxx","Fixed Income","HIGH")),
    ("iboxx € overall 1-3",                  BenchmarkResult("IBOXX_EURO_OVERALL_1_3","iBoxx EUR Overall 1-3Y","iBoxx","Fixed Income","HIGH")),
    ("iboxx € overall",                      BenchmarkResult("IBOXX_EURO_OVERALL","iBoxx EUR Overall","iBoxx","Fixed Income","HIGH")),
    ("iboxx euro corp overall",               BenchmarkResult("IBOXX_EURO_CORP","iBoxx EUR Corporates","iBoxx","Fixed Income","HIGH")),
    ("iboxx euro corporates",                 BenchmarkResult("IBOXX_EURO_CORP","iBoxx EUR Corporates","iBoxx","Fixed Income","HIGH")),
    ("iboxx euro covered",                    BenchmarkResult("IBOXX_EURO_COVERED","iBoxx EUR Covered","iBoxx","Fixed Income","HIGH")),
    ("markit iboxx asian local bond",         BenchmarkResult("IBOXX_ASIA_LOCAL","iBoxx Asian Local Bond","iBoxx","Fixed Income","HIGH")),

    # ── Tipos de interés monetarios ────────────────────────────────────────────
    ("€str",                                  BenchmarkResult("RATE_ESTR","Euro Short-Term Rate (€STR)","ECB","Rate","HIGH")),
    ("estr",                                  BenchmarkResult("RATE_ESTR","Euro Short-Term Rate (€STR)","ECB","Rate","HIGH")),
    ("ester",                                 BenchmarkResult("RATE_ESTR","Euro Short-Term Rate (€STR)","ECB","Rate","HIGH")),
    ("sofr",                                  BenchmarkResult("RATE_SOFR","Secured Overnight Financing Rate (SOFR)","FRB","Rate","HIGH")),
    ("euribor a 3 meses",                     BenchmarkResult("RATE_EURIBOR_3M","EURIBOR 3 Month","EMMI","Rate","HIGH")),
    ("euribor",                               BenchmarkResult("RATE_EURIBOR","EURIBOR","EMMI","Rate","MEDIUM")),
    ("eonia",                                 BenchmarkResult("RATE_EONIA","EONIA","ECB","Rate","HIGH")),

    # ── Otros ─────────────────────────────────────────────────────────────────
    ("dow jones global technology",           BenchmarkResult("DJ_GLOBAL_TECH","Dow Jones Global Technology","S&P/Dow Jones","Equity","HIGH")),
    ("dow jones",                             BenchmarkResult("DJ_INDUSTRIAL","Dow Jones Industrial Average","S&P/Dow Jones","Equity","MEDIUM")),
    ("dax total return",                      BenchmarkResult("DAX_TR","DAX Total Return","Deutsche Börse","Equity","HIGH")),
    ("ibex35",                                BenchmarkResult("IBEX_35","IBEX 35","BME","Equity","HIGH")),
    ("ibex 35",                               BenchmarkResult("IBEX_35","IBEX 35","BME","Equity","HIGH")),
]


# ============================================================
# Motor de normalizacion
# ============================================================

def normalize_benchmark(raw: str) -> Optional[BenchmarkResult]:
    """
    Normaliza un benchmark extraido del KIID al identificador canonico.

    Proceso:
      1. Limpia y filtra falsos positivos (clean_benchmark).
      2. Intenta match por prefijo en ALIAS_MAP (más específico primero).
      3. Si no coincide, elimina sufijos opcionales (index, (net return)...)
         e intenta de nuevo — hasta 4 iteraciones de stripping.

    Devuelve BenchmarkResult o None si no hay coincidencia reconocida.
    """
    cleaned = clean_benchmark(raw)
    if cleaned is None:
        return None

    candidate = cleaned
    for _ in range(4):
        for alias, result in _CANONICAL_DATA:
            if candidate.startswith(alias):
                return result
        stripped = _OPTIONAL_SUFFIX_RE.sub('', candidate).strip()
        stripped = re.sub(r'\s*[,\.;]\s*$', '', stripped).strip()
        if stripped == candidate or len(stripped) < 8:
            break
        candidate = stripped

    return None

def normalize_all(
    raw_list: list[str],
) -> dict[str, Optional[BenchmarkResult]]:
    """
    Normaliza una lista de benchmarks en batch.
    Devuelve dict {raw → BenchmarkResult | None}.
    """
    return {raw: normalize_benchmark(raw) for raw in raw_list}


def get_canonical_ids() -> list[str]:
    """Devuelve la lista de todos los IDs canonicos definidos."""
    return sorted({r.canonical_id for _, r in _CANONICAL_DATA})

# ============================================================
# MAPA MORNINGSTAR — índices propietarios de categoría
# Añadido tras análisis de fund_benchmarks (2425 registros no normalizados)
# Nomenclatura: Gbl=Global, EZN=Eurozone, DM Eur=Dev Europe,
#   EM=Emerging, TME=Total Market Equity, Tgt Alloc=Target Allocation
#   Cau=Cautious, Mod=Moderate, Agg=Aggressive
# ============================================================

_MS_CANONICAL_DATA: list[tuple[str, BenchmarkResult]] = [

    # ── Renta Fija Global ─────────────────────────────────────────────────────
    ("morningstar gbl core bd gr hdg eur",  BenchmarkResult("MS_GBL_CORE_BD_EURH","Morningstar Gbl Core Bond (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl core bd gr hdg usd",  BenchmarkResult("MS_GBL_CORE_BD_USDH","Morningstar Gbl Core Bond (USD Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl core bd gr hdg gbp",  BenchmarkResult("MS_GBL_CORE_BD_GBPH","Morningstar Gbl Core Bond (GBP Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl core bd gr usd",      BenchmarkResult("MS_GBL_CORE_BD","Morningstar Gbl Core Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl corp bd gr hdg eur",  BenchmarkResult("MS_GBL_CORP_BD_EURH","Morningstar Gbl Corp Bond (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl corp bd gr hdg usd",  BenchmarkResult("MS_GBL_CORP_BD_USDH","Morningstar Gbl Corp Bond (USD Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl corp bd gr usd",      BenchmarkResult("MS_GBL_CORP_BD","Morningstar Gbl Corp Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl hy bd gr hdg eur",    BenchmarkResult("MS_GBL_HY_BD_EURH","Morningstar Gbl HY Bond (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl hy bd gr usd",        BenchmarkResult("MS_GBL_HY_BD","Morningstar Gbl HY Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl trsy bd gr hdg eur",  BenchmarkResult("MS_GBL_TRSY_EURH","Morningstar Gbl Treasury Bond (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl trsy inf-lnkd gr hdg eur", BenchmarkResult("MS_GBL_INFL_EURH","Morningstar Gbl Inflation-Linked (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar gbl trsy inf-lnkd gr usd",BenchmarkResult("MS_GBL_INFL","Morningstar Gbl Inflation-Linked","Morningstar","Fixed Income","HIGH")),

    # ── Renta Fija Eurozone ───────────────────────────────────────────────────
    ("morningstar ezn core bd gr eur",      BenchmarkResult("MS_EZN_CORE_BD","Morningstar Eurozone Core Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn corp bd gr eur",      BenchmarkResult("MS_EZN_CORP_BD","Morningstar Eurozone Corp Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn hy bd gr eur",        BenchmarkResult("MS_EZN_HY_BD","Morningstar Eurozone HY Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn trsy bd gr eur",      BenchmarkResult("MS_EZN_TRSY_BD","Morningstar Eurozone Treasury Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn trsy inf-lnkd gr eur",BenchmarkResult("MS_EZN_INFL_BD","Morningstar Eurozone Inflation-Linked","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn 1-3y core bd gr eur", BenchmarkResult("MS_EZN_CORE_BD_1_3","Morningstar Eurozone Core Bond 1-3Y","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn 1-3 yr tsy bd gr eur",BenchmarkResult("MS_EZN_TRSY_1_3","Morningstar Eurozone Treasury 1-3Y","Morningstar","Fixed Income","HIGH")),
    ("morningstar ezn 10+y core bd gr eur", BenchmarkResult("MS_EZN_CORE_BD_10P","Morningstar Eurozone Core Bond 10+Y","Morningstar","Fixed Income","HIGH")),

    # ── Renta Fija USA ────────────────────────────────────────────────────────
    ("morningstar us core bd tr usd",       BenchmarkResult("MS_US_CORE_BD","Morningstar US Core Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar us 1-3y core bd tr usd",  BenchmarkResult("MS_US_CORE_BD_1_3","Morningstar US Core Bond 1-3Y","Morningstar","Fixed Income","HIGH")),
    ("morningstar us 0-1 core exynk tr usd",BenchmarkResult("MS_US_CORE_BD_0_1","Morningstar US Core Bond 0-1Y","Morningstar","Fixed Income","HIGH")),
    ("morningstar us hy bd tr usd",         BenchmarkResult("MS_US_HY_BD","Morningstar US HY Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar us govt bd tr usd",       BenchmarkResult("MS_US_GOVT_BD","Morningstar US Government Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar us corp bd tr usd",       BenchmarkResult("MS_US_CORP_BD","Morningstar US Corp Bond","Morningstar","Fixed Income","HIGH")),

    # ── Renta Fija EM / Asia ──────────────────────────────────────────────────
    ("morningstar em sov bd gr usd",        BenchmarkResult("MS_EM_SOV_BD","Morningstar EM Sovereign Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar em sov bd gr hdg eur",    BenchmarkResult("MS_EM_SOV_BD_EURH","Morningstar EM Sovereign Bond (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar em crp 10% cn cap gr hdg eur", BenchmarkResult("MS_EM_CORP_EURH","Morningstar EM Corp 10% China Cap (EUR Hdg)","Morningstar","Fixed Income","HIGH")),
    ("morningstar em crp 10% cn cap gr usd",BenchmarkResult("MS_EM_CORP","Morningstar EM Corp 10% China Cap","Morningstar","Fixed Income","HIGH")),
    ("morningstar em govt bd lccy gr usd",  BenchmarkResult("MS_EM_GOVT_LCCY","Morningstar EM Govt Bond Local Currency","Morningstar","Fixed Income","HIGH")),
    ("morningstar asia usd brd mkt gr usd", BenchmarkResult("MS_ASIA_USD_BD","Morningstar Asia USD Broad Market","Morningstar","Fixed Income","HIGH")),

    # ── Otros RF ─────────────────────────────────────────────────────────────
    ("morningstar sweden core bd gr sek",   BenchmarkResult("MS_SWEDEN_CORE_BD","Morningstar Sweden Core Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar swzld core bd gr chf",    BenchmarkResult("MS_SWISS_CORE_BD","Morningstar Switzerland Core Bond","Morningstar","Fixed Income","HIGH")),
    ("morningstar uk corp bd gr gbp",       BenchmarkResult("MS_UK_CORP_BD","Morningstar UK Corp Bond","Morningstar","Fixed Income","HIGH")),

    # ── Monetario / Cash ──────────────────────────────────────────────────────
    ("morningstar eur 1m cash gr eur",      BenchmarkResult("MS_EUR_1M_CASH","Morningstar EUR 1M Cash","Morningstar","Rate","HIGH")),
    ("morningstar usd 1m cash tr usd",      BenchmarkResult("MS_USD_1M_CASH","Morningstar USD 1M Cash","Morningstar","Rate","HIGH")),
    ("morningstar gbp 1m cash gr gbp",      BenchmarkResult("MS_GBP_1M_CASH","Morningstar GBP 1M Cash","Morningstar","Rate","HIGH")),
    ("morningstar chf 1m cash gr chf",      BenchmarkResult("MS_CHF_1M_CASH","Morningstar CHF 1M Cash","Morningstar","Rate","HIGH")),

    # ── Mixtos / Target Allocation ────────────────────────────────────────────
    ("morningstar eu mod gbl tgt alloc nr eur",  BenchmarkResult("MS_EU_MOD_ALLOC","Morningstar EU Moderate Global Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar eu cau gbl tgt alloc nr eur",  BenchmarkResult("MS_EU_CAU_ALLOC","Morningstar EU Cautious Global Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar eu agg gbl tgt alloc nr eur",  BenchmarkResult("MS_EU_AGG_ALLOC","Morningstar EU Aggressive Global Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar eu mod tgt alloc nr eur",      BenchmarkResult("MS_EU_MOD_ALLOC_EUR","Morningstar EU Moderate Allocation EUR","Morningstar","Mixed","HIGH")),
    ("morningstar eu cau tgt alloc nr eur",      BenchmarkResult("MS_EU_CAU_ALLOC_EUR","Morningstar EU Cautious Allocation EUR","Morningstar","Mixed","HIGH")),
    ("morningstar eu agg tgt alloc nr eur",      BenchmarkResult("MS_EU_AGG_ALLOC_EUR","Morningstar EU Aggressive Allocation EUR","Morningstar","Mixed","HIGH")),
    ("morningstar eaa usd mod tgt alloc nr usd", BenchmarkResult("MS_USD_MOD_ALLOC","Morningstar EAA USD Moderate Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar eaa usd cau tgt alloc nr usd", BenchmarkResult("MS_USD_CAU_ALLOC","Morningstar EAA USD Cautious Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar eaa usd agg tgt alloc nr usd", BenchmarkResult("MS_USD_AGG_ALLOC","Morningstar EAA USD Aggressive Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar us mod tgt alloc nr usd",      BenchmarkResult("MS_US_MOD_ALLOC","Morningstar US Moderate Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar us con tgt alloc nr usd",      BenchmarkResult("MS_US_CON_ALLOC","Morningstar US Conservative Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar uk mod tgt alloc nr gbp",      BenchmarkResult("MS_UK_MOD_ALLOC","Morningstar UK Moderate Allocation","Morningstar","Mixed","HIGH")),
    ("morningstar uk cau tgt alloc nr gbp",      BenchmarkResult("MS_UK_CAU_ALLOC","Morningstar UK Cautious Allocation","Morningstar","Mixed","HIGH")),

    # ── Renta Variable Global ─────────────────────────────────────────────────
    ("morningstar global tme nr usd",       BenchmarkResult("MS_GBL_EQ","Morningstar Global Equity","Morningstar","Equity","HIGH")),
    ("morningstar global all cap tme nr usd",BenchmarkResult("MS_GBL_ALL_CAP","Morningstar Global All Cap Equity","Morningstar","Equity","HIGH")),
    ("morningstar gbl growth tme nr usd",   BenchmarkResult("MS_GBL_GROWTH","Morningstar Global Growth Equity","Morningstar","Equity","HIGH")),
    ("morningstar gbl val tme nr usd",      BenchmarkResult("MS_GBL_VALUE","Morningstar Global Value Equity","Morningstar","Equity","HIGH")),
    ("morningstar gbl high div yld nr usd", BenchmarkResult("MS_GBL_HIGH_DIV","Morningstar Global High Dividend Yield","Morningstar","Equity","HIGH")),
    ("morningstar gbl smid nr usd",         BenchmarkResult("MS_GBL_SMID","Morningstar Global Small/Mid Cap","Morningstar","Equity","HIGH")),
    ("morningstar us market ext nr usd",    BenchmarkResult("MS_GBL_ALL_CAP","Morningstar Global All Cap Equity","Morningstar","Equity","MEDIUM")),

    # ── Renta Variable Temática Global ───────────────────────────────────────
    ("morningstar gbl tech nr usd",         BenchmarkResult("MS_GBL_TECH","Morningstar Global Technology","Morningstar","Equity","HIGH")),
    ("morningstar gbl health nr usd",       BenchmarkResult("MS_GBL_HEALTH","Morningstar Global Healthcare","Morningstar","Equity","HIGH")),
    ("morningstar gbl renew enrg nr usd",   BenchmarkResult("MS_GBL_RENEW","Morningstar Global Renewable Energy","Morningstar","Equity","HIGH")),
    ("morningstar gbl eq infra nr usd",     BenchmarkResult("MS_GBL_INFRA","Morningstar Global Infrastructure","Morningstar","Equity","HIGH")),
    ("morningstar gbl real est tme nr usd", BenchmarkResult("MS_GBL_REAL_EST","Morningstar Global Real Estate","Morningstar","Equity","HIGH")),
    ("morningstar gbl util - reg water gr usd", BenchmarkResult("MS_GBL_WATER","Morningstar Global Utilities/Water","Morningstar","Equity","HIGH")),
    ("morningstar gbl fin svc nr usd",      BenchmarkResult("MS_GBL_FINANCIALS","Morningstar Global Financial Services","Morningstar","Equity","HIGH")),
    ("morningstar gbl biotechnology nr usd",BenchmarkResult("MS_GBL_BIOTECH","Morningstar Global Biotechnology","Morningstar","Equity","HIGH")),
    ("morningstar gbl enrg nr usd",         BenchmarkResult("MS_GBL_ENERGY","Morningstar Global Energy","Morningstar","Equity","HIGH")),
    ("morningstar gbl cons cyc tme gr usd", BenchmarkResult("MS_GBL_CONS_CYC","Morningstar Global Consumer Cyclical","Morningstar","Equity","HIGH")),
    ("morningstar gbl com svc nr usd",      BenchmarkResult("MS_GBL_COMM_SVC","Morningstar Global Communication Services","Morningstar","Equity","HIGH")),
    ("morningstar gbl upstm nat res nr usd",BenchmarkResult("MS_GBL_NAT_RES","Morningstar Global Natural Resources","Morningstar","Equity","HIGH")),
    ("morningstar gbl agricul inputs nr usd",BenchmarkResult("MS_GBL_AGRI","Morningstar Global Agriculture","Morningstar","Equity","HIGH")),
    ("morningstar global bas mat tme nr usd",BenchmarkResult("MS_GBL_MATERIALS","Morningstar Global Basic Materials","Morningstar","Equity","HIGH")),
    ("morningstar gbl gold nr usd",         BenchmarkResult("MS_GBL_GOLD","Morningstar Global Gold","Morningstar","Equity","HIGH")),

    # ── Renta Variable USA ────────────────────────────────────────────────────
    ("morningstar us large-mid nr usd",     BenchmarkResult("MS_US_LM","Morningstar US Large/Mid Cap","Morningstar","Equity","HIGH")),
    ("morningstar us lm brd value nr usd",  BenchmarkResult("MS_US_VALUE","Morningstar US Large/Mid Value","Morningstar","Equity","HIGH")),
    ("morningstar us lm brd growth nr usd", BenchmarkResult("MS_US_GROWTH","Morningstar US Large/Mid Growth","Morningstar","Equity","HIGH")),
    ("morningstar us mid nr usd",           BenchmarkResult("MS_US_MID","Morningstar US Mid Cap","Morningstar","Equity","HIGH")),
    ("morningstar us small extended nr usd",BenchmarkResult("MS_US_SMALL","Morningstar US Small Cap Extended","Morningstar","Equity","HIGH")),
    ("morningstar us high div yld nr usd",  BenchmarkResult("MS_US_HIGH_DIV","Morningstar US High Dividend Yield","Morningstar","Equity","HIGH")),

    # ── Renta Variable Europa ─────────────────────────────────────────────────
    ("morningstar dm eur tme nr eur",       BenchmarkResult("MS_EU_EQ","Morningstar Developed Europe Equity","Morningstar","Equity","HIGH")),
    ("morningstar dev ezn tme nr eur",      BenchmarkResult("MS_EZN_EQ","Morningstar Developed Eurozone Equity","Morningstar","Equity","HIGH")),
    ("morningstar dev europe grt tme nr eur",BenchmarkResult("MS_EU_GROWTH","Morningstar Developed Europe Growth","Morningstar","Equity","HIGH")),
    ("morningstar dev europe val tme nr eur",BenchmarkResult("MS_EU_VALUE","Morningstar Developed Europe Value","Morningstar","Equity","HIGH")),
    ("morningstar dev eur sml tme nr eur",  BenchmarkResult("MS_EU_SMALL","Morningstar Developed Europe Small Cap","Morningstar","Equity","HIGH")),
    ("morningstar dev eur smid tme nr eur", BenchmarkResult("MS_EU_SMID","Morningstar Developed Europe Small/Mid","Morningstar","Equity","HIGH")),
    ("morningstar dm eur xuk tme nr eur",   BenchmarkResult("MS_EU_EX_UK","Morningstar Developed Europe ex-UK","Morningstar","Equity","HIGH")),
    ("morningstar dm eur div yld >2.5% nr eur", BenchmarkResult("MS_EU_DIV","Morningstar Developed Europe High Dividend","Morningstar","Equity","HIGH")),
    ("morningstar dm eur real est nr eur",  BenchmarkResult("MS_EU_REAL_EST","Morningstar Developed Europe Real Estate","Morningstar","Equity","HIGH")),
    ("morningstar dev ezn smid tme nr eur", BenchmarkResult("MS_EZN_SMID","Morningstar Developed Eurozone Small/Mid","Morningstar","Equity","HIGH")),
    ("morningstar germany tme nr eur",      BenchmarkResult("MS_GERMANY","Morningstar Germany Equity","Morningstar","Equity","HIGH")),
    ("morningstar germany tme gr eur",      BenchmarkResult("MS_GERMANY","Morningstar Germany Equity","Morningstar","Equity","HIGH")),
    ("morningstar italy nr eur",            BenchmarkResult("MS_ITALY","Morningstar Italy Equity","Morningstar","Equity","HIGH")),
    ("morningstar spain tme nr eur",        BenchmarkResult("MS_SPAIN","Morningstar Spain Equity","Morningstar","Equity","HIGH")),
    ("morningstar nordic tme gr eur",       BenchmarkResult("MS_NORDIC","Morningstar Nordic Equity","Morningstar","Equity","HIGH")),
    ("morningstar uk all cap tme nr gbp",   BenchmarkResult("MS_UK_EQ","Morningstar UK All Cap Equity","Morningstar","Equity","HIGH")),
    ("morningstar dev eur xuk small tme nr eur", BenchmarkResult("MS_EU_EX_UK_SMALL","Morningstar Developed Europe ex-UK Small","Morningstar","Equity","HIGH")),

    # ── Renta Variable Asia-Pacífico ──────────────────────────────────────────
    ("morningstar asia xjpn tme nr usd",    BenchmarkResult("MS_ASIA_EX_JP","Morningstar Asia ex-Japan Equity","Morningstar","Equity","HIGH")),
    ("morningstar asia xjpn tme gr usd",    BenchmarkResult("MS_ASIA_EX_JP","Morningstar Asia ex-Japan Equity","Morningstar","Equity","HIGH")),
    ("morningstar apac tme nr usd",         BenchmarkResult("MS_APAC","Morningstar Asia Pacific Equity","Morningstar","Equity","HIGH")),
    ("morningstar apac xjpn tme nr usd",    BenchmarkResult("MS_APAC_EX_JP","Morningstar Asia Pacific ex-Japan","Morningstar","Equity","HIGH")),
    ("morningstar apac xjpn dyf gr usd",    BenchmarkResult("MS_APAC_EX_JP_DIV","Morningstar Asia Pacific ex-Japan Dividend","Morningstar","Equity","HIGH")),
    ("morningstar dev apac xjpn tme nr usd",BenchmarkResult("MS_DEV_APAC_EX_JP","Morningstar Developed APAC ex-Japan","Morningstar","Equity","HIGH")),
    ("morningstar japan tme nr jpy",        BenchmarkResult("MS_JAPAN","Morningstar Japan Equity","Morningstar","Equity","HIGH")),
    ("morningstar japan grt tme nr jpy",    BenchmarkResult("MS_JAPAN_GROWTH","Morningstar Japan Growth Equity","Morningstar","Equity","HIGH")),
    ("morningstar japan sml nr jpy",        BenchmarkResult("MS_JAPAN_SMALL","Morningstar Japan Small Cap","Morningstar","Equity","HIGH")),
    ("morningstar china tme nr usd",        BenchmarkResult("MS_CHINA","Morningstar China Equity","Morningstar","Equity","HIGH")),
    ("morningstar china tme gr usd",        BenchmarkResult("MS_CHINA","Morningstar China Equity","Morningstar","Equity","HIGH")),
    ("morningstar india tme nr usd",        BenchmarkResult("MS_INDIA","Morningstar India Equity","Morningstar","Equity","HIGH")),
    ("morningstar korea tme nr usd",        BenchmarkResult("MS_KOREA","Morningstar Korea Equity","Morningstar","Equity","HIGH")),
    ("morningstar taiwan tme nr twd",       BenchmarkResult("MS_TAIWAN","Morningstar Taiwan Equity","Morningstar","Equity","HIGH")),
    ("morningstar hong kong tme gr usd",    BenchmarkResult("MS_HONG_KONG","Morningstar Hong Kong Equity","Morningstar","Equity","HIGH")),
    ("morningstar asean tme nr usd",        BenchmarkResult("MS_ASEAN","Morningstar ASEAN Equity","Morningstar","Equity","HIGH")),

    # ── Renta Variable EM ─────────────────────────────────────────────────────
    ("morningstar em tme nr usd",           BenchmarkResult("MS_EM_EQ","Morningstar Emerging Markets Equity","Morningstar","Equity","HIGH")),
    ("morningstar em smid tme nr usd",      BenchmarkResult("MS_EM_SMID","Morningstar Emerging Markets Small/Mid","Morningstar","Equity","HIGH")),
    ("morningstar em americas tme nr usd",  BenchmarkResult("MS_EM_AMERICAS","Morningstar Emerging Americas Equity","Morningstar","Equity","HIGH")),
    ("morningstar brazil tme nr usd",       BenchmarkResult("MS_BRAZIL","Morningstar Brazil Equity","Morningstar","Equity","HIGH")),
    ("morningstar middle east & africa nr usd", BenchmarkResult("MS_MEA","Morningstar Middle East & Africa","Morningstar","Equity","HIGH")),

    # ── Otros (Switzerland, Sweden...) ───────────────────────────────────────
    ("morningstar switzerland tme nr chf",  BenchmarkResult("MS_SWITZERLAND","Morningstar Switzerland Equity","Morningstar","Equity","HIGH")),

    # ── Otros proveedores no Morningstar ─────────────────────────────────────
    ("markit iboxx eur corp subordinated tr", BenchmarkResult("IBOXX_EUR_CORP_SUB","iBoxx EUR Corp Subordinated","iBoxx","Fixed Income","HIGH")),
    ("markit iboxx albi tr usd",            BenchmarkResult("IBOXX_ALBI","iBoxx Asian Local Bond Index","iBoxx","Fixed Income","HIGH")),
    ("markit iboxx albi china offshore tr cnh", BenchmarkResult("IBOXX_ALBI_CN_OFF","iBoxx ALBI China Offshore","iBoxx","Fixed Income","HIGH")),
    ("markit iboxx albi china onshore tr cny", BenchmarkResult("IBOXX_ALBI_CN_ON","iBoxx ALBI China Onshore","iBoxx","Fixed Income","HIGH")),
    ("refinitiv global hgd cb tr eur",      BenchmarkResult("REFINITIV_GBL_CB_EURH","Refinitiv Global Conv Bond (EUR Hdg)","Refinitiv","Mixed","HIGH")),
    ("refinitiv europe cb tr eur",          BenchmarkResult("REFINITIV_EU_CB","Refinitiv Europe Conv Bond","Refinitiv","Mixed","HIGH")),
    ("ice bofa asiandollar hycp cn is tr usd", BenchmarkResult("ICE_ASIA_USD_HY_CN","ICE BofA Asia Dollar HY Corp China Is.","ICE BofA","Fixed Income","HIGH")),
    ("ftse global cb tr usd",               BenchmarkResult("FTSE_GLOBAL_CB_USD","FTSE Global Conv Bond (USD)","FTSE","Mixed","HIGH")),
    ("bloomberg pan euro agg tr eur",       BenchmarkResult("BBG_PAN_EURO_AGG","Bloomberg Pan-European Aggregate","Bloomberg","Fixed Income","HIGH")),
    ("cat 50%jpm embi plus tr&50%msci em nr", BenchmarkResult("CAT_50_EMBI_50_MSCI_EM","50% JPM EMBI / 50% MSCI EM","Custom","Mixed","MEDIUM")),
    ("cat 75%citi swissgbi&25%msci wld free nr", BenchmarkResult("CAT_75_SWISS_25_WORLD","75% Citi Swiss GBI / 25% MSCI World","Custom","Mixed","MEDIUM")),
    ("cat 40%citi swissgbi&60%msci wld free nr", BenchmarkResult("CAT_40_SWISS_60_WORLD","40% Citi Swiss GBI / 60% MSCI World","Custom","Mixed","MEDIUM")),

    # ── BL-BENCH-NORM v4.1: cola no normalizada (236 filas) ───────────────────
    # Orden: específico ANTES que base (el matcher usa startswith, primer match gana).
    # MSCI Europe (estilos específicos antes de la base)
    ("msci europe value",   BenchmarkResult("MSCI_EUROPE_VALUE","MSCI Europe Value","MSCI","Equity","MEDIUM")),
    ("msci europe growth",  BenchmarkResult("MSCI_EUROPE_GROWTH","MSCI Europe Growth","MSCI","Equity","MEDIUM")),
    ("msci europe small",   BenchmarkResult("MSCI_EUROPE_SMALL","MSCI Europe Small/Mid Cap","MSCI","Equity","MEDIUM")),
    ("msci europe",         BenchmarkResult("MSCI_EUROPE","MSCI Europe","MSCI","Equity","MEDIUM")),
    # MSCI otras regiones
    ("msci usa value",      BenchmarkResult("MSCI_USA_VALUE","MSCI USA Value","MSCI","Equity","MEDIUM")),
    ("msci usa",            BenchmarkResult("MSCI_USA","MSCI USA","MSCI","Equity","MEDIUM")),
    ("msci united kingdom", BenchmarkResult("MSCI_UK","MSCI United Kingdom","MSCI","Equity","MEDIUM")),
    ("msci nordic",         BenchmarkResult("MSCI_NORDIC","MSCI Nordic","MSCI","Equity","MEDIUM")),
    ("msci ac as",          BenchmarkResult("MSCI_AC_ASIA","MSCI AC Asia","MSCI","Equity","LOW")),  # truncado en origen
    # ICE BofA (HY y subfamilias antes de la base)
    ("ice bofa us high yield",                BenchmarkResult("ICE_BOFA_HY","ICE BofA US High Yield","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa european currency high yield", BenchmarkResult("ICE_BOFA_HY","ICE BofA European Currency High Yield","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa asian dollar high yield",      BenchmarkResult("ICE_BOFA_HY","ICE BofA Asian Dollar High Yield","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa estr",                         BenchmarkResult("ESTR","ICE BofA ESTR Overnight","ICE BofA","Money Market","MEDIUM")),
    ("ice bofa green bond",                   BenchmarkResult("ICE_BOFA_GREEN","ICE BofA Green Bond","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa global",                       BenchmarkResult("ICE_BOFA_GLOBAL","ICE BofA Global","ICE BofA","Fixed Income","LOW")),
    ("ice bofa euro",                         BenchmarkResult("ICE_BOFA_EUR","ICE BofA Euro","ICE BofA","Fixed Income","MEDIUM")),
    ("ice bofa",                              BenchmarkResult("ICE_BOFA","ICE BofA","ICE BofA","Fixed Income","LOW")),
    # JP Morgan
    ("jp morgan emerging markets bond", BenchmarkResult("JPM_EMBI","JP Morgan EMBI","JP Morgan","Fixed Income","MEDIUM")),
    ("jpmorgan jaci",                   BenchmarkResult("JPM_JACI","JP Morgan Asia Credit (JACI)","JP Morgan","Fixed Income","MEDIUM")),
    ("jp morgan",                       BenchmarkResult("JPM_BOND","JP Morgan Bond","JP Morgan","Fixed Income","LOW")),
    ("jpmorgan",                        BenchmarkResult("JPM_BOND","JP Morgan Bond","JP Morgan","Fixed Income","LOW")),
    # Russell
    ("russell",  BenchmarkResult("RUSSELL","Russell","Russell","Equity","LOW")),
    # Bloomberg Euro Aggregate
    ("bloomberg euro-aggregate corporate", BenchmarkResult("BBG_EUR_AGG_CORP","Bloomberg Euro Aggregate Corporate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg euro ag",                  BenchmarkResult("BBG_EUR_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","MEDIUM")),
    ("bloomberg euro",                     BenchmarkResult("BBG_EUR_AGG","Bloomberg Euro Aggregate","Bloomberg","Fixed Income","LOW")),
    # S&P regionales
    ("s&p global mining",        BenchmarkResult("SP_GLOBAL_MINING","S&P Global Mining","S&P","Equity","MEDIUM")),
    ("s&p pan arab",             BenchmarkResult("SP_PAN_ARAB","S&P Pan Arab Composite","S&P","Equity","MEDIUM")),
    ("s&p japan mid small cap",  BenchmarkResult("SP_JAPAN_SMID","S&P Japan Mid/Small Cap","S&P","Equity","MEDIUM")),
    ("s&p eurozone",             BenchmarkResult("SP_EUROZONE","S&P Eurozone","S&P","Equity","MEDIUM")),
    # Tipos overnight cortos (pairs con kiid_parser BL-38-v21: SOFR/€STR/ESTR ya cubiertos)
    ("sonia",  BenchmarkResult("RATE_SONIA","SONIA Overnight","Bank of England","Money Market","HIGH")),
    ("tona",   BenchmarkResult("RATE_TONA","TONA Overnight","Bank of Japan","Money Market","HIGH")),
    ("saron",  BenchmarkResult("RATE_SARON","SARON Overnight","SIX","Money Market","HIGH")),
    ("ester",  BenchmarkResult("RATE_ESTR","€STR (ESTER) Overnight","ECB","Money Market","HIGH")),
]


def _normalize_benchmark_extended(raw: str) -> Optional[BenchmarkResult]:
    """
    Extiende normalize_benchmark con el mapa Morningstar y otros proveedores.
    Se llama como fallback cuando normalize_benchmark devuelve None.

    Estrategia de matching en dos pasadas:
    1. Comparar el nombre completo limpiado (con sufijo NR/GR/TR + divisa)
    2. Comparar sin el sufijo (para variantes con divisa diferente)
    """
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip().lower()
    candidate = re.sub(r'\s+', ' ', candidate)

    # Pasada 1: nombre completo
    for alias, result in _MS_CANONICAL_DATA:
        if candidate.startswith(alias) or candidate == alias:
            return result

    # Pasada 2: sin sufijo NR/GR/TR + divisa al final
    stripped = re.sub(
        r'\s+(nr|gr|tr)\s+(usd|eur|gbp|jpy|chf|cnh|cny|sek|aud|twd)\s*$',
        '', candidate
    ).strip()
    if stripped != candidate:
        for alias, result in _MS_CANONICAL_DATA:
            alias_stripped = re.sub(
                r'\s+(nr|gr|tr)\s+(usd|eur|gbp|jpy|chf|cnh|cny|sek|aud|twd)\s*$',
                '', alias
            ).strip()
            if stripped.startswith(alias_stripped) or stripped == alias_stripped:
                return result

    return None


# Monkey-patch: extender normalize_benchmark para incluir mapa Morningstar
_orig_normalize = normalize_benchmark

def normalize_benchmark(raw: str) -> Optional[BenchmarkResult]:
    result = _orig_normalize(raw)
    if result is not None:
        return result
    return _normalize_benchmark_extended(raw)
