# proyecto1/core/classify_utils.py
# -*- coding: utf-8 -*-
"""
Utilidades de clasificacion compartidas por todos los bloques de P1.
Version 3 — canonico v2 + ventana KIID correcta

FUNCIONES:
  Señales de nombre (constantes):
    NAME_SIGNALS_MONETARIO, NAME_SIGNALS_RF_CORTO, NAME_SIGNALS_RF_FLEXIBLE,
    NAME_SIGNALS_MIXTO, NAME_SIGNALS_RV, NAME_SIGNALS_ALTERNATIVO,
    NAME_SIGNALS_ESTRUCTURADO

  Deteccion por nombre:
    detect_nature_from_name(name_l)           → str | None
    detect_geography(name_l)                  → str | None
    detect_theme(name_l)                      → str | None
    detect_is_esg(fund_name)                  → int  (0/1)
    detect_style_profile(name_l)              → str | None
    detect_exposure_bias(name_l, fund_nature) → str | None
    detect_strategy(replication_method, subtype, name_l) → str | None
    detect_benchmark_type(benchmark_declared, replication_method) → str | None
    detect_profile_from_srri(srri)            → str | None

  Deteccion por texto KIID (ventana correcta 1200-4500):
    detect_nature_from_kiid(kiid_text)        → str | None
    detect_type_from_kiid(kiid_text, fund_nature) → str | None
    detect_style_from_kiid(kiid_text)         → str | None
    detect_geography_from_kiid(kiid_text)     → str | None
    detect_esg_from_kiid(kiid_text)           → int  (0/1)
    detect_ongoing_charge_from_kiid(kiid_text) → float | None
    detect_kiid_attributes(kiid_text, fund_nature) → dict

  Dominios:
    FUND_NATURES, TYPE_BY_NATURE, BIAS_ALLOWED_NATURES
"""

import re
from typing import Optional


# ============================================================
# Dominios canónicos v2
# ============================================================

FUND_NATURES: frozenset = frozenset({
    "Monetario", "Renta Fija Corto Plazo", "Renta Fija Flexible",
    "Renta Variable", "Mixto", "Alternativo", "Restantes",
})

BIAS_ALLOWED_NATURES: frozenset = frozenset({
    "Renta Fija Corto Plazo", "Renta Fija Flexible",
    "Renta Variable", "Alternativo", "Restantes",
})

TYPE_BY_NATURE: dict = {
    "Monetario":              frozenset({"CNAV","LVNAV","VNAV","Enhanced Cash"}),
    "Renta Fija Corto Plazo": frozenset({"Gobierno CP","Crédito CP","Floating Rate",
                                          "Covered Bond","Ultrashort"}),
    "Renta Fija Flexible":    frozenset({"Corporativo","Gobierno","High Yield","Emergentes",
                                          "Inflación","Convertible","Multisector",
                                          "Unconstrained","Target Maturity"}),
    "Renta Variable":         frozenset({"Gestión Activa","Indexado","ETF","Smart Beta"}),
    "Mixto":                  frozenset({"Allocation","Target Volatility","Target Outcome",
                                          "Tactical","Lifecycle"}),
    "Alternativo":            frozenset({"Absolute Return","Long/Short","Market Neutral",
                                          "Sistemático/CTA","Commodities","Real Assets",
                                          "Estructurado"}),
    "Restantes":              frozenset({"Estructurado","Capital Protegido",
                                          "Fondo de Fondos"}),
}

# Mapeo interno → canónico para _detect_nature
_NATURE_CANONICAL: dict = {
    "Monetario":     "Monetario",
    "RF_Corto":      "Renta Fija Corto Plazo",
    "RF_Flexible":   "Renta Fija Flexible",
    "Renta Variable":"Renta Variable",
    "Mixtos":        "Mixtos",
    "Alternativo":   "Alternativo",
    "Estructurado":  "Restantes",
}


# ============================================================
# SEÑALES DE NOMBRE — constantes compartidas
# ============================================================
# Cada bloque primario importa su lista.
# restantes.py las usa todas vía detect_nature_from_name().
# Nuevos patrones identificados en análisis de 527 restantes:
#   RFF: 'bond ', 'bonds ', 'bd ', 'bnds ', ' debt ', 'tot ret',
#        'sh durat', 'dur bond', 'ig cred', 'fix inc horizon',
#        'corp bd', 'hy bnd', 'green bond'
#   RFC: 'sh durat bd', 'dur bond ', 'fix inc horizon'

NAME_SIGNALS_MONETARIO: list = [
    # Amundi Liquidity family (LIQ / LQ)
    "liq select", "liq st gov", "liq-rtd", "liq rtd",
    "lq sh trm", "lq sh term", "lq-rtd", "eu liq st",
    # BlackRock ICS (Institutional Cash)
    "ics liq", "ics euro liq", "ics usd liq", "ics gov liq",
    "ics ul sh cor", "ic admin iii", "ics usd liq pr",
    # Money Market nombres directos
    "euro money mkt", "euro money mk",
    "mon mrkt fnd", "lux mon mrkt",
    "st mm vnav", "liqud usd st mm",
    "st money mket", "short-term mm",
    "s-t money mkt", "s-t money mk",
    # Français
    "tresor court", "court terme",
    "entreprises n ", "entreprises r ",
    # Otros
    "institut liq",
    "euro liq reserv", "liq reserv",
    "inst esq euro money", "geldmarkt",
    # BNP InstiCash / Pictet Sovereign MM / Amundi Cash
    "insticash", "instica eur",
    "pictet sov", "sov st mney", "sover.sho",
    "amundi cash", "amundi fds cash",
    # Nuevos — análisis Restantes (señales específicas sin falso positivo)
    "euro m mkt",                    # JPM EURO M MKT VNAV (evita EM MKT)
    "standard mm vnav",              # JPM STANDARD MM VNAV
    "lqudty lvnav",                  # JPM USD LQUDTY LVNAV (OCR)
    "inscash",                       # BNP PARIBS INSCASH EUR 3M
    "gbp liq lvnav",                 # JPM GBP LIQ LVNAV
    "gbp liq cnav",                  # variante CNAV
    "usd treasur cnav",              # JPM USD TREASURY CNAV
    "usd liq cnav",                  # JPM USD LIQ CNAV
    "fidelity euro cash",            # FIDELITY EURO CASH
    "fidelity fund us cash",         # FIDELITY FUND US CASH
    "fidelity us cash",              # variante
]

NAME_SIGNALS_RF_CORTO: list = [
    # Español explícito
    "corto plazo", "cs corto", "cs duracion 0",
    # Ultrashort / low duration
    "ult sh term", "ul sh cor", "ul sh tr",
    "ult st t bd", "ul shor fix in",
    "invesco ult sh",
    # Bonos flotantes
    "float rate notes", "float rate nt",
    # Covered bonds
    "covered bond", "cov bond", "pfandbrief",
    # Short maturity / short duration names
    "euro bonds short", "eur bonds short",
    "euro st bnd", "eur st bnd",
    "sh durat crd", "short durat crd",
    "crdt vr shrt trm",
    "vontobel euro sh term",
    "ms short mat", "ms sicav short mat",
    "short mat bd",
    "eurozone flexib",
    "duracion 0-",
    "euro sh term b", "eur sh term b",
    "sisf euro st bnd",
    # Allianz floating rate / enhanced short term
    "allianz fl r nt", "allianz flo rate nots",
    "allianz g flt rt", "allianz glb flt",
    "allianz enh sh term", "allianz enhanc.shor",
    # Covered bonds con abreviatura
    "coverd bnd", "cov bnd", "cov bond",
    "nordea low dur europ", "nordea low dur eurp", "nordea lowdur",
    # Nuevos — análisis 527 restantes
    "sh durat bd", "eur sh durat bd",
    "dur bond",                                  # BGF EURO SHORT DUR BOND
    "fix inc horizon",                           # DWS Fixed Income Horizon 2026/2027
    "bgf eur sh durat",
    "euro bonds short",
    "bonds short",
    "esg euro bonds short",
    # Nuevos — análisis Restantes
    "shor.durat",                    # AXA WF EURO CREDI.SHOR.DURAT (OCR con punto)
    "crd shor dur",                  # AXA WF EURO CRD SHOR DUR
    "credi.shor",                    # AXA WF EURO CREDI.SHOR (variante OCR)
    "flo rate nts",                  # CANDRIAM FLO RATE NTS (floating rate notes)
    "flot rate nts",                 # variante OCR
    # Nuevos — análisis Restantes v2
    "dws float rate not",            # DWS FLOAT RATE NOTE (floating rate)
    "dws float r. note",             # variante OCR
    "float rate not",                # genérico floating rate notes
    "jpm euro gvrmnt short dur",     # JPM EURO GOVERNMENT SHORT DURATION
    "jpm eur gvrmnt short",          # variante
    "gl.short.dur.in",               # FIDELITY GL.SHORT.DURATION INCOME (OCR puntos)
    "fidelity gl.short",             # variante
]

NAME_SIGNALS_RF_FLEXIBLE: list = [
    # Bond genérico — patrones cortos (ordenados de más específico a más general)
    "euro bond", "eurobond", "eur bond",
    "corporate bond", "corp bond",
    "government bond", "gov bond", "govt bond",
    "high yield", " hy bond", " hy bd",
    "convertible bond", "convertibles",
    "aggregate bond",
    "emerging market debt", "em debt",
    "total return bond",
    "flexible bond", "dynamic bond",
    # Alemán
    "renten",
    # Francés/fondo income
    "rend plus", "rendement",
    "convic crdit", "convic credit",
    # Maturity bond strategy
    "millesima", "millesim", "milles select",
    "cat bond",
    "financial bond", "financ bond",
    "stiftungsfonds",
    "securite eur",
    "global bond", "gl bond", "glob bond",
    "euro corp bond", "eur corp bond",
    "euro gov bond", "eur gov bond",
    # Gestoras específicas
    "pictet eur bond", "pictet eur corp", "pictet chf bond",
    "pictet usd gov", "pictet eur bonds", "pictet glob emrg debt",
    "pictet usd gov bond",
    "pimco euro bond", "pimco gl bond", "pimco tot ret", "pimco total ret",
    "pimco euro bond", "pimco glbl ig", "pimco glb ig", "pimco gl ig",
    "pimco glob ig", "pimco glbl ig credit", "pimco gl grad crd",
    "pimco unconstra", "pimco mtgage", "pimco low aver dur",
    "pimco div inc", "pimco diversif",
    "ishr gv bd", "ishr inv cp bd", "ishr em g bon",
    "vgd eu gov bd", "vgd euro gov bd", "vgd us gov",
    "vgd gbl bd", "vgd gbl bond", "vgd gbl sh term", "vgd gl st corp",
    "vgd us inv crd", "vgd us inv gde",
    "asian tiger bond",
    "sisf euro bond", "sisf euro gov bond", "sisf euro corp",
    "sisf euro high yield", "sisf euro st bnd",
    "sisf glob inflat", "sisf gl inflt", "sisf global infl",
    "sisf strategic", "sisf gl cred", "sisf glob cred", "sisf glbal cred",
    "sisf gl inflt lnkd", "sisf glob credit",
    "bnp euro bond", "bnp euro corp", "bnp euro gov",
    "bnp paribas e c bd", "bnp paribas e cr bd", "bnp paribas e h y",
    "bnp paribas ecbsp",
    "nordea europ covered bond", "nordea low dur europ",
    "nordea 1 europ coverd", "nordea 1 em corpor",
    "nordea 1 norw", "nordea 1 swdish", "nordea 1 swed",
    "nordea 1 swedish", "nordea 1 us corp",
    "robeco high yield",
    "ubam float rate",
    "la francaise",
    "r-co target", "r-co conv crdi",
    "rfmi multig",
    "ostrum sri euro bond",
    "candriam bond",
    "invesco euro bond", "invesco europ bond", "invesco multisect",
    "invesco glob tot ret",
    "invesco euro corp", "invesco european bond",
    "franklin euro high yield", "franklin euro hy",
    "franklin eu tot ret", "franklin euro gov",
    "ftgf west gl m strat", "ftgf wine gl fix",
    "templeton asia", "templeton asian bond",
    "templeton emer mkt bond", "templeton gl bond",
    "templeton gl tot ret", "templeton sust glb",
    "fidelity euro bond", "fidelity em mkt debt",
    "fidelity asian bond", "fidelity eur s-t bond",
    "fidelity euro shor", "fidelity f.eur bond",
    "fidelity f.int bond", "fidelity flex bond",
    "fidelity f.emer mkt debt", "fidelity strat bond",
    "fidelity us dollar bond",
    "bgf euro bond", "bgf gl hy bond", "bgf usd high yield",
    "bgf asian tiger bond", "bgf euro short durat",
    "bgf china bond", "bgf euro corp bond",
    "bgf fixed inc gl opp", "bgf fix inc gl",
    "bgf world bond", "bgf glob f asian tiger",
    "blackrock esg corp b", "blackrock esg f i st",
    "blackrock esg fis", "blackrock esgcorp",
    "blackrock esgfis", "bsf em mk flx",
    "is em g bon index", "ishr em mkt gv",
    "ishr eme mk gv", "ishr gv bd indx",
    "ishr inv cp bd indx",
    "ubs lux bond", "ubs bond eur", "ubs asia flexible",
    "ubs asian high yield", "ubs china fix",
    "ubs bd sicav shrt t",
    "ms sicav euro bond", "ms sicav euro corp",
    "ms sicav euro strat", "ms eu corp bd",
    "ms inv em mkt dbt", "mss emrging mkt debt",
    "ms short maturity",
    "mfs em mkt debt", "mfs m eme mkt debt",
    "mfs global opp bond", "mfs us gov bond",
    "mfs m.emer mkt debt",
    "m&g lux em market bond", "m&g lux euro corp bond",
    "m&g lux glob fr hy", "m&g gl flot rate hy",
    "janus h hf euro corp", "janus hend hor bond",
    "jpm em market debt", "jpm emrg mkt corp bd",
    "jpm us aggre bond", "jpm em markts debt",
    "jpm emrg mkt corp",
    "jupiter m emerg m debt", "jupiter m emerg",
    "gam star cat bond", "gam star gl rates",
    "gam str em mk b op",
    "gs euro long dur bond", "gs euromix bond",
    "gs euromix", "gs gl strat macr bd",
    "gs glob str macr bnd", "gs glob stra macro bnd",
    "gs green bond",
    "edmond rot mil",
    "edr bond alloc", "edr financial bonds",
    "carmignac cred", "carmignac credit",
    "carmignac p flex bon", "carmignac port flex bond",
    "carmignac prt fl bon", "carmignac securite",
    "carmignac pr.scurit",
    "deutsche invest asian bnds", "dws asian bonds",
    "dws asian bnds", "dws china bnds", "dws china bonds",
    "dws covered bond",
    "dws euro corp bnds", "dws euro corp bond",
    "dws euro corporate bond", "dws euro corporate bonds",
    "dws euro hy corp", "dws euro hy corporates",
    "dws inv as bond", "dws inv as bonds",
    "dws inv esg asian", "dws inv esg eu corp",
    "dws invest asian bonds",
    "dws strt esg aloc",
    "dws esg euro bonds",
    "dws esg dyn opport",
    "dws esg eurp smamid",
    "db fix inc opp",
    "fix inc horizon",
    "ssga eu cp bd esg",
    "asteria funds",
    "arcano inc esg", "arcano lowvo",
    "vontobel 24 strat", "vontobel gl act bond",
    "schroder glb crdt", "schroder isf corp",
    "schroder isf euro bond", "schroder isf sec c",
    "schroder isf sec cr",
    "af us sh term bond",
    "ab fix mat",
    "ab mortgage incom",
    "threadn lux cred", "threadneedle cred",
    # DWS Convertibles
    "dws esg convertibles", "dws convertibles",
    # Nuevos — análisis 527 restantes (tokens frecuentes no cubiertos)
    " bond ",                                    # genérico: "FIDELITY EURO BOND FUND"
    " bonds ",                                   # genérico: "AXA WF EUR STRAT BONDS"
    " bd ",                                      # abrev: "BGF CHINA BOND D2"
    " bnds ",                                    # abrev: "AXA WF GL INFLAT BNDS"
    " debt ",                                    # "FIDELITY EM MKT DEBT"
    "tot ret bnd",                               # "INVESCO GLOB TOT RET BND"
    " hy bnd ",                                  # "BGF USD HY BND"
    "corp bd",                                   # "JPM EMRG MKT CORP BD"
    "ig cred", " ig cr",                         # "PIMCO GLB IG CRED"
    "inv grade cr", "inv gde cr",                # Vanguard Investment Grade Credit
    "green bond", "act green",                   # AXA WF ACT GREEN (green bond fund)
    "env clim", "environ clim",                  # Invesco Environmental Climate
    "sub bond", "subordi",                       # DWS Corporate Hybrid / Subordinated
    "hybrid bond", "hybrid bnds",
    "buy&watch", "buy & watch", "buywat",
    "amundi rend plus", "amundi ult st t bd",
    "amundi ul sh tr bd", "amundi funds us bond",
    "amundi funds gbl sub",
    # Nuevos — análisis Restantes
    "inflat.bond", "inflat bond",    # AXA WF GLOBAL INFLAT.BONDS (inflation-linked)
    "global inflat",                 # AXA WF GLOBAL INFLATIO.BONDS
    "euro credit plus", "eur credit plus",  # AXA WF EUR CREDIT PLUS
    "medium trm bnd", "medium term bnd",  # BNP P. EUR MEDIUM TRM BND
    "euro corporat bnd",             # BNP P. EURO CORPORAT BND
    "sust eme mk",                   # CANDRIAM SUST EME MK (sustainable EM bond)
    "sust emerg",                    # variante
    "allianz green bond",            # ALLIANZ GREEN BOND
    "allianz euro crd",              # ALLIANZ EURO CRD SRI
    "allianz euro credit",           # ALLIANZ EURO CREDIT SRI
    "us high yie.bond",              # AXA WF US HIGH YIE.BOND (OCR con punto)
    "axa wf e m s d bn",             # AXA WF E M S D BN (EM Short Duration Bond)
    # Nuevos — análisis Restantes v2
    "fidelity china hy",             # FIDELITY CHINA HY (China high yield)
    "ab gb hy pf",                   # AB GLOBAL HIGH YIELD PORTFOLIO
    "dws inv corp gr bon",           # DWS INVEST CORPORATE GROWTH BONDS
    "dws inv esg eu hy",             # DWS ESG EU HIGH YIELD
    "dws eurorenta",                 # DWS EURORENTA (European bond)
    "dws float r",                   # DWS FLOAT RATE → RF_Corto ya cubierto
    "gs us dollar crdt",             # GS US DOLLAR CREDIT
    "gs us dollar credit",           # variante
    "axa sd hy low carbon",          # AXA SHORT DURATION HY LOW CARBON
    "pictet asia loc cur",           # PICTET ASIA LOCAL CURRENCY DEBT
    "pictet asian local cur",        # variante
    "amundi core eur gov bnd",       # AMUNDI CORE EUR GOVERNMENT BOND
    "jpm euro gov st dur",           # JPM EURO GOVERNMENT SHORT DURATION → ya en RF_Corto
    "invesco pan eu hi",             # INVESCO PAN EUROPEAN HIGH INCOME
    "invesco pn eur hi",             # variante OCR
    "carmignac prt credit",          # CARMIGNAC PORTFOLIO CREDIT
    "gam star crd",                  # GAM STAR CREDIT OPPORTUNITIES
    "gam star credit opp",           # variante
    # Análisis Restantes v3
    "ab americ inc portf",           # AB AMERICAN INCOME (Bloomberg US Agg)
    "ab americn inc port",           # variante OCR
    "ab fcp amer incm",              # variante
    "afs buy & wat inc",             # AFS BUY & WATCH INCOME (target maturity)
    "axa wf gib",                    # AXA WF GLOBAL INFLATION BOND REDEX
    "axa wf glb inf bn rdx",         # variante
    "candriam sus bn em m",          # CANDRIAM SUSTAINABLE BOND EM
    "candriam sustain bnd eur",      # CANDRIAM SUSTAINABLE BOND EUR
    "dws inv eurogov bnd",           # DWS INVEST EURO GOVERNMENT BOND
    "dws inv nzt eur c b",           # DWS NET ZERO TARGET CORP BOND
    "edr mill select",               # EDR MILLESIMA SELECT (target maturity credit)
    "fidelity f.europ.high y",       # FIDELITY EUROPE HIGH YIELD
    "fidelity us highyield",         # FIDELITY US HIGH YIELD
    "franklin strt inc",             # FRANKLIN STRATEGIC INCOME
    "ftgf west asian oppo",          # FTGF WESTERN ASSET ASIAN BOND
    "ftgf west asian",               # variante
    "fvs ii rentas",                 # FlossBach RENTAS (income bond)
    "gs em mks corp bnd",            # GS EM CORPORATE BOND
    "gs em mkt hard curr",           # GS EM HARD CURRENCY bond
    "gs gbl hy",                     # GS GLOBAL HIGH YIELD
    "templeton gl.t.ret",            # TEMPLETON GLOBAL TOTAL RETURN
    "templeton glb tot ret",         # variante
    "templeton glo tot ret",         # variante
    "templeton glob.total ret",      # variante
    "bnp paribas e jpm segdctp",     # BNP JPM SECURITIZED
    "axa wf e mk s d b",             # AXA WF EM SHORT DURATION BOND
]

NAME_SIGNALS_MIXTO: list = [
    "patrimoine", "patrimoin",
    "m. expert",
    "conservador fi", "moderado fi", "crecimiento fi",
    "prem equilib",
    "str fund blced", "str fund yield",
    "strat fund blced",
    "patrim bal", "patrim def", "patrim agressiv",
    "glbal balncd",
    "patrimonial def", "patrimonial bal",
    "global resili", "glob resili",
    "us balancd",
    "fidelity ma dyn",
    "templeton gb val",
    "ubs str fund",
    "ab american inc",
    "balancd",                       # JANUS H US BALANCD 2026 (balanced)
    "m&g optimal inc",               # M&G OPTIMAL INCOME (multi-asset income)
    "fvs m asset", "fvs multi", "fvs multiple",
    "fvs ii equilib",
    # DB / Deutsche SAA y multi-opp
    "db cnsrvativ saa", "db balancd saa", "db best all",
    "db sia balanc", "db sia consrvtv", "db sia eur",
    "db sia usd", "db priv markt",
    "deutsche multiopport", "dws multi opp",
    # DWS Kaldemorgen (famous multi-asset)
    "kaldemorgen", "dws concept kalde", "dws cncpt kalde",
    "dws con kalde", "dws con.kal",
    "dws invest cons opp",
    # BGF Multi-Asset
    "bgf esg multiass", "bgf gl m asset inc",
    "bgf dyn high inc",
    "bsf gl event driv",
    # DWS ESG Climate / Real Assets
    "dws inv esg clim op", "dws inv esg real as",
    "dws esg blue eco", "dws c.esg blue",
    "dws strt esg aloc",
    # GS Patrimonial
    "gs patrim bal", "gs em debt",
    "gs glob hy ocs", "gs glob hy",
    # JPM Global Bond / Corp Bond
    "jpm gl bond opp", "jpm glob corp bond",
    "jpm global corpo", "jpmorgan gl.corp",
    # Otros
    "bsf em cies", "m&g dyn alloc",
    "m&g episode macro", "m&g glob convrtb",
    "allianz dmas sri", "allianz dy st sri",
    "janus.h. us forty", "janus h us forty",
    "r-co valor", "r-co thematic",
    "guinness gl eq inc",
    "allianz strategy",
    # Allianz Orient Income / Mixto income-oriented
    "allianz orient inc",
    # AF Pioneer Flexible Opportunities
    "af pioneer flexible",
    # Multicop Sicav (multi-asset CH)
    "multicop sicav",
    # JPM Global Macro (macro multi-asset)
    "jpm glob macro", "jpm us sh duration",
    # Carmignac Patrimoine (multi-asset conservador)
    "carmignac pfl ptr", "carmignac prtfl ptr",
    "carmignac emergi. patrim", "carmignac emerg.patrim",
    "carmignac prt ptr",
    # Nuevos — análisis Restantes
    "carmignac patrim",              # CARMIGNAC PATRIM A USDHDG (variante OCR corta)
    "allianz inc & grow",            # ALLIANZ INC & GROW (income & growth multi-asset)
    "allianz inc & growt",           # variante OCR
    "db cnsrvatv saa",               # DB CNSRVATV SAA (conservative SAA)
    "db sia consvtv",                # DB SIA CONSVTV (conservative)
    "dje gestion patrimon",          # DJE GESTION PATRIMONIAL
    # Análisis Restantes v3
    "allianz dy ma stg",             # ALLIANZ DYNAMIC MULTI-ASSET STRATEGY
    "amundi protect 90",             # AMUNDI PROTECT 90 (capital protection)
    "dws fm esg m.a.def",            # DWS MULTI-ASSET DEFENSIVE 2026
    "dws esg stftngsfds",            # DWS STIFTUNGSFONDS (balanced foundation)
    "dws fund esg garant",           # DWS ESG GARANT (guaranteed mixed)
    "fidelity mltasset inc",         # FIDELITY MULTI-ASSET INCOME
    "fidelity target 202",           # FIDELITY TARGET 2025/2030 (lifecycle)
    "franklin us mangd inc",         # FRANKLIN US MANAGED INCOME
    "gs (l) patrim",                 # GS PATRIMOINE
    "fvs ii rentas rt",
    "balanced",          # JANUS US BALANCED 2026,              # FlossBach RENTAS II (balanced income)
]


NAME_SIGNALS_RV: list = [
    "akkumula", "deutschland", "aktien", "aktn st",
    "wellcare", "smart ind tec",
    "artificial intelligenc",
    "osteuropa", "russia",
    "silver age", "silverplus",
    "thematic silverplus", "thematic real estat",
    "pictet water", "pictet digital",
    "global brands", "glob brands",
    "global focus", "glob focus",
    "carmignac investis", "carmignac invest",
    "carmignac emergent", "carmignac grand",
    "carmignac grand eurp",
    "fidelity america", "fidelity germany",
    "fidelity ital", "fidelity iberia",
    "fidelity world fund", "fidelity greater china",
    "fidelity latin", "fidelity gl finan",
    "fidelity glb consum", "fidelity glob indust",
    "fidelity f jap", "fidelity f asi",
    "gs eur eq", "gs eurozone eq", "gs em mkt eq",
    "gs japn eq", "gs gbl eq",
    "dws india", "dws invest top",
    "dws invest top asia", "dws invest top euroland",
    "dws invst esq tp eurlnd",
    "dws esg eurp smamid cap",
    "r-co valor", "r-co thematic",
    "h2o adagio sp",
    "nordea glob stbl equi",
    "liontrust gf str eq",
    "cpr silver age",
    "guinness gl eq",
    "harris ass glbal eq",
    "polar c gl insur", "polar glob insur",
    "polar cap artif intel", "polar capital bio",
    "polar capital gbl tch",
    "templeton gl clima", "templeton eastern euro",
    "trowe price us", "trowe px glob foc", "trowe px us blue",
    "gqg partner em",
    "robeco emerg market eq",
    "magna fiera cap", "magna mena",
    "optimized eq incom",
    "morgan st glbal brands", "morganstanley us grow",
    "ms em eurp mideast", "ms in f asia",
    "findlay park",
    "janus h emerg mkt",
    "janus h hf peur sm com",
    "janus hh gl tec lead", "janus paneu sm comp",
    "janus h paneu sm comp",
    "janus h hf peur prop",
    "pictet glb env oppts",
    "pictet security",
    # BGF fondos sin keyword equity explícito
    "bgf asian dragon", "bgf latin americ",
    "bgf asian grow lead", "bgf asian grw lead",
    "bgf cont europ flex", "bgf contin.europ",
    "bgf eur eqity trans", "bgf euro market",
    "bgf europ.special", "bgf future transport",
    "bgf jap sm mid", "bgf japan smallmid",
    "bgf next gen tec", "bgf syst glb small",
    "bgf united kingdom", "bgf us basic val",
    "bgf us flexible eq", "bgf us opportunit",
    "bgf us small & mid", "bgf world energy",
    "blackrock gbl uncnst",
    # DWS / Allianz equity sin keyword
    "dws artif intellig", "dws inv artif intel",
    "dws inv artif intell",
    "allianz china a-shr", "allianz china a sh",
    "allianz thematica", "allianz mult ast fut",
    "allianz eu eq grow",
    # Otros equity sin keyword
    "gam m luxury brand", "gam ms luxury brand",
    "janus us sm cap val",
    # HSBC GIF equity (sin keyword equity en nombre)
    "hsbc asia ex jap", "hsbc gif asia ex",
    "hsbc gif brazil", "hsbc gif chinese",
    "hsbc gif euroland", "hsbc gif frontier",
    "hsbc gif hong kong", "hsbc gif indian",
    "hsbc gif idian", "hsbs gif thai",
    "hsbc gif thai",
    # Templeton Japan — equity sin keyword
    "templeton japan",
    # GAM Luxury Brands (typo brnds)
    "gam m luxury brnds", "luxury brnds",
    # BGF variantes con typos OCR
    "bgf futur transport", "bgf future of transport",
    "bgf us flexibl eq",
    # Amundi Polen (growth equity)
    "amundi polen",
    # Azvalor Blue Chips
    "azvalor blue chips",
    # Nuevos — análisis Restantes (señales equity sin keyword explícito)
    "croci",                         # DWS CROCI (factor equity, Deutsche)
    "top div",                       # DWS ESG EU TOP DIV / DEUTSCHE EUROP TOP DIVIDEND
    "top dividend",                  # variante completa
    "world financials",              # BGF WORLD FINANCIALS
    "world tchnlgy",                 # BGF WORLD TCHNLGY (OCR)
    "world healtscnc",               # BGF WORLD HEALTSCNC (OCR)
    "dynam eq",                      # BGF SUST GL DYNAM EQ
    "sust gl dynam",                 # variante
    "glob real estat sec",           # DEUTSCHE GLB REAL ESTAT SEC
    "amundi indx",                   # AMUNDI INDX MSCI WORLD / EU CORP (indexed)
    "amundi ind msci",               # AMUNDI IND MSCI WRLD
    "amundi s&p 500 scr",            # AMUNDI S&P 500 SCRND (screened index)
    "amundi sp500",                  # variante OCR
    "amundi m nam",                  # AMUNDI M NAM ESG (North America equity)
    "amundi msci na",                # AMUNDI MSCI NA ESG
    "amundi core msci",              # AMUNDI CORE MSCI EM MKTS (indexed equity)
    "amundi core msc",               # variante OCR
    "vgd esg em mkt eq",             # VGD ESG EM MKT EQ INDX (Vanguard indexed equity)
    "low vol world",                 # DEUTSCHE QNT LOW VOL WORLD
    "qnt low vol",                   # variante
    "emrging mkt top div",           # DWS EMRGING MKT TOP DIV
    "us top divid",                  # DEUTSCHE II US TOP DIVID
    "us top dividend",               # variante completa
    "glob environment",              # BNP GLOB ENVIRONMENT (clean energy equity)
    "clean en sol",                  # BNP FUND CLEAN EN SOL (clean energy)
    "sust gbl eqy",                  # BNP P. SUST GBL EQY
    "enhan in eq", "enh in eq",      # AXA enhanced index equity
    "us en in eq", "us enh in eq",   # AXA US enhanced index equity
    "us eq alpha",                   # AXA R US EQ ALPHA
    "switzerland eq",                # AXA WF SWITZERLAN EQ
    "switzerland a acc",             # variante
    "candriam eq l",                 # CANDRIAM EQ L ONCO / EURP INN (equity long)
    "europ top div",                 # variante
    "eur mdium trm",                 # no — esto es RF, quitar
    "ab select us eq",               # AB SELECT US EQ (AllianceBernstein US equity)
    "ab low volatlity eq",           # AB LOW VOLATILITY EQ
    "ab sust. gl. thematic",         # AB SUST GL THEMATIC (global equity)
    "deutsche europ top",            # DEUTSCHE EUROP TOP DIVIDEND
    "deutsche ii glb eq",            # DEUTSCHE II GLB EQ
    "deutsche ii us top",            # DEUTSCHE II US TOP DIVID
    "dws emrging mkt top",           # DWS EMRGING MKT TOP DIV
    "dws croci",                     # DWS CROCI (ya cubierto por "croci")
    "dws dje alpha",                 # DWS DJE ALPHA RNTN (absolute return equity)
    "dws esg eu top div",            # DWS ESG EU TOP DIV
    "dws esg gl em eq",              # DWS ESG GL EM EQ
    "dws esg gen infras",            # DWS ESG GEN INFRAST (infrastructure equity)
    "dws critic tec",                # DWS CRITIC TEC (critical tech equity)
    "bgf world",                     # BGF WORLD sector funds (financials, tech, health)
    "bgf sust gl",                   # BGF SUST GL DYNAM EQ
    "azvalor internat",              # AZVALOR INTERNAT (value equity)
    "ct lux sust glb eq",            # CT LUX SUST GLB EQ INC
    "fidelity fast em mkt",          # FIDELITY FAST EM MKT (EM equity)
    "sisf gl em mkt",                # SISF GL EM MKT OPPO (EM equity)
    "gs em mkt currency",            # GS EM MKT CURRENCY (EM currency/bond — RF_Flex)
    "jpm em mkt sma cap",            # JPM EM MKT SMA CAP (EM small cap equity)
    # Fix inconsistencias fund_family_builder
    "amundi euroland eq",            # FAM_000121: RV no Monetario
    "jpm us value",                  # FAM_001697: RV Value no Alternativo
    "thematics safety",              # FAM_001897: RV temática (seguridad)
    "gs us equity",                  # FAM_001385/386: RV con/sin hedge
    "templeton global income",       # FAM_001293: RV Income no Mixtos
    "gs gbl eq income",              # FAM_001343: RV Income no Mixtos
    "smart food",                    # FAM_000513: BNP SMART FOOD → RV temática (food)
    "bnp p. smart food",             # variante con punto OCR
    # Nuevos — análisis Restantes v2 (350+ fondos identificados)
    "trowe px", "trowe price",       # T. Rowe Price equity
    "jpm us select eq", "jpm us slct eq",
    "jpm china a shar",              # JPM China A Shares
    "jpm us small cap grow", "jpm us sm cap",
    "jpm asia pacific eq", "jpm asia pacif",
    "jpm emerg mkt eq", "jpm emerg mkt opp",
    "jpm greater china", "jpm japan esg eq",
    "jpm them gen ther",             # JPM Thematic Genomics
    "fidelity asia pac opp", "fidelity asia small",
    "fidelity china consmr", "fidelity china cons",
    "fidelity f as eq",              # Fidelity Asia ESG Equity
    "fidelity f ftr conn",           # Fidelity Future Connectivity
    "fidelity f glb dv",             # Fidelity Global Dividend Plus
    "fidelity f wt &w", "fidelity f wter",   # Fidelity Water & Waste
    "fidelity japan indx", "fidelity msci world",
    "fast asia",                     # Fidelity FAST Asia
    "robeco sust water",             # Robeco Sustainable Water
    "robeco smart energy",           # Robeco Smart Energy
    "robeco smart mobilit",          # Robeco Smart Mobility
    "robeco bp gl",                  # Robeco BP Global Premium Equity
    "pictet jap eq",                 # Pictet Japan Equity
    "pictet china",                  # Pictet China Equity
    "pictet clean en",               # Pictet Clean Energy Transition
    "pictet nutrition",              # Pictet Nutrition (thematic)
    "pictet premium brands",         # Pictet Premium Brands
    "pictet gl megatr",              # Pictet Global Megatrend
    "nordea 1 em s steqf", "nordea 1 gl s steqf",
    "nordea 1 gl clim",              # Nordea Global Climate & Environment
    "nordea 1 glob stbl equi",       # Nordea Global Stable Equity
    "schroder isf china", "sisf china",
    "sisf asian opp",                # SISF Asian Opportunities
    "sisf frontier mkt eq",          # SISF Frontier Markets Equity
    "sisf us smal&mid cap eq",       # SISF US Small & Mid Cap
    "sisf europ.divid",              # SISF European Dividend
    "schroder isf g a e",            # Schroder ISF Global Alt Energy
    "dws inv esg clm", "dws inv esg dyn",
    "dws inv esg top", "dws inv esg gl",
    "dws concept dje",               # DWS Concept DJE
    "dws gbl infrastr", "dws invest glbl infras",
    "janus h hor gl",                # Janus Henderson Horizon Global
    "janus h hf euroland", "janus h hf peur",
    "janus h gl tec", "janus h gl sm f",
    "ubs lux dig hlth", "ubs lux dig hlt",
    "ubs ai and rob", "ubs lux ai",  # UBS AI & Robotics
    "ubs lux sec eq",                # UBS Security Equity
    "ms em leaders equ",             # MS EM Leaders Equity
    "mss asia opportunity",          # Morgan Stanley Asia Opportunity
    "harris ass. glbal",             # Harris Associates Global Equity
    "first eagle amundi int",        # First Eagle Amundi International
    "first eag amnd int",            # variante OCR (amnd=amundi)
    "first eag amun int",            # variante OCR
    "first eag.amun.int",            # variante OCR con punto
    "ftgf clearbridge", "ftgf r us sm cap",
    "ftgf wine gl", "ftgf put lg cap val",
    "gs gl futur gen eq",            # GS Global Future Generation
    "gs gbl core eqy",               # GS Global Core Equity
    "gs em mrkt eq",                 # GS EM Market Equity
    "invesco gl cons tr",            # Invesco Global Consumer Trends
    "invesco pan eu sys eq",         # Invesco Pan European Systematic
    "invesco pn eur eq",             # Invesco Pan European Equity
    "templeton frntr mkt",           # Templeton Frontier Markets
    "franklin u.s. opp",             # Franklin US Opportunities
    "index msci world",              # Index MSCI World
    "medtch", "medtech",           # Vontobel MedTech equity
]


NAME_SIGNALS_ALTERNATIVO: list = [
    "absolute return", "abs ret", "absret",
    "arb strat", "arbit strat",
    "tiede", "tiedm",
    "lyxor t arb", "lyxor t arbit",
    "candriam index arbi",
    "gam star alpha spe",
    "gam star gl rates",
    "h2o adagio r ",
    "jupiter m. glb eq abs ret",
    "jupiter m. emerg m",
    "jupiter st absret",
    "pimco multiassut",
    "pimco bal inc grw",
    "schroder isf sec crd", "schroder isf sec c",
    # BSF / BlackRock absolute return
    "blackrock sf europ",
    "bsf em cies abs ret",
    "bsf europ.opp.ext",
    # EDR Millesima (target maturity bond — Alt border)
    "edr millesim", "edmond rot mil",
    # Carmignac Patrimoine — se mantiene en Mixtos pero la variante
    # "portfolio patrimoine" con exposición absoluta va aquí
    "carmignac pfl ptr", "carmignac prtfl ptr",
    # Nuevos — análisis Restantes
    "enhanc comod",                  # CTHREAD ENHANC COMOD (enhanced commodity)
    "enhanced comod",                # variante completa
    "dws enh commdty",               # DWS ENH COMMDTY STRT (enhanced commodity)
    "bsf europ opp ext",             # BSF EUROP OPP EXTENSION (absolute return)
    "fram dig ecom",                 # AXA WF FRAM DIG ECOM (digital economy long/short)
    "fram dig econ",                 # variante
    # Nuevos — análisis Restantes v2
    "franklin alt st",               # FRANKLIN ALT STRATEGIES (multi-strategy)
    "janus h glob ls",               # JANUS HENDERSON GLOBAL LONG/SHORT
    "janus h hf",                    # JANUS HENDERSON HEDGE FUND long/short
    "nordea 1 alpha 10",             # NORDEA ALPHA 10 MA (vol-target 10%)
    "nordea 1 stable ret",           # NORDEA STABLE RETURN (multi-asset AR)
    "nordea 1 stable retu",          # variante OCR
    "schroder gaia bluetr",          # SCHRODER GAIA BLUETREND (CTA sistemático)
    "schroder gaiablue",             # variante
    "mfs prudent capital",           # MFS PRUDENT CAPITAL → mixtos/AR
    "thread enhanc commod",          # THREAD ENHANC COMMOD (ya en lista)
    "invesco balan.risk",            # INVESCO BALANCED RISK ALLOCATION
    # Análisis Restantes v3
    "gs q bbg comm. index",          # GS Q BLOOMBERG COMMODITY INDEX
    "gs q comm ix prtf",             # variante OCR
    "gs q m st bbg cm",              # variante OCR
    # Análisis Restantes v4
    "nordea 1 active rts opt",       # en RF_Flex pero no en Alt (activo tasas interés)
    "nordea 1 alpha 15",             # NORDEA ALPHA 15 (vol target multi-asset)
    "nordea 1 active",               # variante
    "gamco merger arbit",            # GAMCO MERGER ARBITRAGE (event-driven)
    "thread.gl dy rl re",            # THREADNEEDLE GLOBAL DYNAMIC REAL RETURN
    "thread.glob dynam real",        # variante
    # ── Nuevas señales P1 ──────────────────────────────────────────────────────
    "bsf europ opp ext",             # BSF EUROP OPP EXTENSION (equity extension strategy)
]

NAME_SIGNALS_ESTRUCTURADO: list = [
    "autocall", "capital protected",
    "capital protection", "capital guarantee",
]


# ============================================================
# detect_nature_from_name — fuente única para todos los bloques
# ============================================================

def _name_match(name_l: str, signals: list) -> bool:
    return any(s in name_l for s in signals)


def detect_nature_from_name(name_l: str) -> Optional[str]:
    """
    Detecta la naturaleza del fondo solo desde el nombre (en minúsculas).
    Devuelve el valor interno ('Monetario', 'RF_Corto', 'RF_Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo', 'Estructurado') o None.

    Orden: Estructurado > Alternativo > Monetario > RF_Corto >
           Mixtos > RF_Flexible > Renta Variable
    (RF_Flexible antes de RV para evitar que 'bond' en nombres temáticos
     bloquee la detección de equity)
    """
    if _name_match(name_l, NAME_SIGNALS_ESTRUCTURADO):
        return "Estructurado"
    if _name_match(name_l, NAME_SIGNALS_ALTERNATIVO):
        return "Alternativo"
    if _name_match(name_l, NAME_SIGNALS_MONETARIO):
        return "Monetario"
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"
    if _name_match(name_l, NAME_SIGNALS_MIXTO):
        return "Mixtos"
    if _name_match(name_l, NAME_SIGNALS_RF_FLEXIBLE):
        return "RF_Flexible"
    if _name_match(name_l, NAME_SIGNALS_RV):
        return "Renta Variable"
    return None


# ============================================================
# detect_nature_from_kiid — ventana correcta 1200-4500
# ============================================================

_WINDOW_OBJ_START = 1200   # inicio sección objetivo de inversión (KIID clásico)
_WINDOW_OBJ_END   = 4500   # fin sección objetivo / inicio riesgos (KIID clásico)
_WINDOW_COST_START = 9000  # inicio sección costes
_WINDOW_COST_END   = 14000 # fin sección costes

# Ventanas por formato de documento
# KIID clásico (UCITS pre-2023): objetivo en 1200-4500
# DDF/PRIIPs  (post-2023):       objetivo en 200-2000 (sección "Finalidad" temprana)
# UNKNOWN:                        ventana amplia 200-4500 por seguridad
_WINDOWS_BY_FORMAT: dict = {
    "KIID":    (1200, 4500),
    "DDF":     (500,  5000),   # Ampliado a 5000: algunos DDF tienen objetivo en pos 4500-4800
    "UNKNOWN": (200,  4500),
}


def _detect_kiid_format(text: str) -> str:
    """
    Detecta el formato del documento KIID.

    Devuelve:
        'DDF'     — formato PRIIPs/DDF (post-2023), sección objetivo en 500-4500
        'KIID'    — formato KIID clásico UCITS, sección objetivo en 1200-4500
        'UNKNOWN' — formato no reconocido, ventana amplia 200-4500
    """
    if not text:
        return "UNKNOWN"
    header = text[:600].lower()

    # DDF/PRIIPs — varias variantes de detección:
    # 1. Cadena continua (caso normal)
    # 2. OCR fusionado sin espacios (JPMorgan/Amundi)
    if ("documento de datos fundamentales" in header
            or "documentodedatosfundamentales" in header):
        return "DDF"
    # DDF partido: "Finalidad" + "Producto" al inicio (JPMorgan OCR por lineas)
    if "finalidad" in header[:150] and "producto" in header[:400]:
        return "DDF"

    # KIID clásico UCITS
    if any(sig in header for sig in [
        "datos fundamentales para el inversor",
        "key investor information document",
        "informações fundamentais destinadas",
        "informações fundamentais ao investidor",
    ]):
        return "KIID"

    return "UNKNOWN"


def _get_obj_bounds(text: str) -> tuple[int, int]:
    """Devuelve (start, end) de la ventana objetivo según el formato del documento."""
    fmt = _detect_kiid_format(text)
    return _WINDOWS_BY_FORMAT[fmt]


def _extract_window(text: str, start: int, end: int) -> str:
    """Extrae ventana segura del texto."""
    return text[start:end] if len(text) > start else ""


def detect_nature_from_name(name_l: str) -> Optional[str]:
    """
    Detecta la naturaleza del fondo solo desde el nombre (en minúsculas).
    Devuelve el valor interno ('Monetario', 'RF_Corto', 'RF_Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo', 'Estructurado') o None.

    Orden: Estructurado > Alternativo > Monetario > RF_Corto >
           Mixtos > RF_Flexible > Renta Variable
    (RF_Flexible antes de RV para evitar que 'bond' en nombres temáticos
     bloquee la detección de equity)
    """
    if _name_match(name_l, NAME_SIGNALS_ESTRUCTURADO):
        return "Estructurado"
    if _name_match(name_l, NAME_SIGNALS_ALTERNATIVO):
        return "Alternativo"
    if _name_match(name_l, NAME_SIGNALS_MONETARIO):
        return "Monetario"
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"
    if _name_match(name_l, NAME_SIGNALS_MIXTO):
        return "Mixtos"
    if _name_match(name_l, NAME_SIGNALS_RF_FLEXIBLE):
        return "RF_Flexible"
    if _name_match(name_l, NAME_SIGNALS_RV):
        return "Renta Variable"
    return None


# ============================================================
# detect_nature_from_kiid — ventana correcta 1200-4500
# ============================================================

_WINDOW_OBJ_START = 1200   # inicio sección objetivo de inversión (KIID clásico)
_WINDOW_OBJ_END   = 4500   # fin sección objetivo / inicio riesgos (KIID clásico)
_WINDOW_COST_START = 9000  # inicio sección costes
_WINDOW_COST_END   = 14000 # fin sección costes

# Ventanas por formato de documento
# KIID clásico (UCITS pre-2023): objetivo en 1200-4500
# DDF/PRIIPs  (post-2023):       objetivo en 200-2000 (sección "Finalidad" temprana)
# UNKNOWN:                        ventana amplia 200-4500 por seguridad
_WINDOWS_BY_FORMAT: dict = {
    "KIID":    (1200, 4500),
    "DDF":     (500,  5000),   # Ampliado a 5000: algunos DDF tienen objetivo en pos 4500-4800
    "UNKNOWN": (200,  4500),
}


def _detect_kiid_format(text: str) -> str:
    """
    Detecta el formato del documento KIID.

    Devuelve:
        'DDF'     — formato PRIIPs/DDF (post-2023), sección objetivo en 500-4500
        'KIID'    — formato KIID clásico UCITS, sección objetivo en 1200-4500
        'UNKNOWN' — formato no reconocido, ventana amplia 200-4500
    """
    if not text:
        return "UNKNOWN"
    header = text[:600].lower()

    # DDF/PRIIPs — varias variantes de detección:
    # 1. Cadena continua (caso normal)
    # 2. OCR fusionado sin espacios (JPMorgan/Amundi)
    if ("documento de datos fundamentales" in header
            or "documentodedatosfundamentales" in header):
        return "DDF"
    # DDF partido: "Finalidad" + "Producto" al inicio (JPMorgan OCR por lineas)
    if "finalidad" in header[:150] and "producto" in header[:400]:
        return "DDF"

    # KIID clásico UCITS
    if any(sig in header for sig in [
        "datos fundamentales para el inversor",
        "key investor information document",
        "informações fundamentais destinadas",
        "informações fundamentais ao investidor",
    ]):
        return "KIID"

    return "UNKNOWN"


def _get_obj_bounds(text: str) -> tuple[int, int]:
    """Devuelve (start, end) de la ventana objetivo según el formato del documento."""
    fmt = _detect_kiid_format(text)
    return _WINDOWS_BY_FORMAT[fmt]


def _extract_window(text: str, start: int, end: int) -> str:
    """Extrae ventana segura del texto."""
    return text[start:end] if len(text) > start else ""


def detect_nature_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta la naturaleza del fondo desde el texto KIID.
    Usa la ventana correcta (1200-4500) donde está la sección de objetivo.

    Devuelve el valor interno ('Monetario', 'RF_Corto', 'RF_Flexible',
    'Renta Variable', 'Mixtos', 'Alternativo', 'Estructurado') o None.

    Prioridad: Estructurado > Monetario > Alternativo > RF_Corto >
               RF_Flexible (dominante) > RV (dominante) > Mixtos >
               RF_Flexible (señal débil)
    """
    if not kiid_text:
        return None

    t = kiid_text.lower()
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(t, _obj_start, _obj_end)

    # ── Señales en encabezado (nombre del producto) ─────────────────────────
    # El nombre del producto aparece en los primeros 600 chars y puede contener
    # señales de tipo aunque el texto esté fusionado (OCR sin espacios JPMorgan)
    _header = t[:600]
    _has_equity_in_header = (
        "equity" in _header          # "AsiaPacificEquityFund", "EquityFund"
        or "equities" in _header
        or "renta variable" in _header
    )
    _has_bond_in_header = (
        "bond" in _header
        or "bonds" in _header
        or "fixed income" in _header
        or "aggregate" in _header    # Bloomberg Aggregate
        or "credit" in _header
        or "renta fija" in _header
    )

    # ── Texto OCR fusionado: buscar señales con regex sin espacios ────────────
    import re as _re
    _ocr_equity = bool(
        _re.search(r'invirtiendo\s*principalmente\s*en\s*compa', t)
        or _re.search(r'invirtiendo\s*en\s*acciones', t)
        or _re.search(r'invierte\s*principalmente\s*en\s*acciones', t)
        or _re.search(r'principalmenteenacciones', t)
        or _re.search(r'medianteinvirtiendoen', t)
    )

    # ── Estructurado (buscar en todo el texto) ───────────────────────────────
    if any(k in t for k in [
        "autocall", "autocallable", "capital protected", "capital protection",
        "capital guarantee", "capital garantizado", "structured note",
        "nota estructurada", "barrier", "knock-in", "knock in",
    ]):
        return "Estructurado"

    # ── Monetario ────────────────────────────────────────────────────────────
    if any(k in t[:2000] for k in [
        "money market fund", "fondo del mercado monetario", "fondo monetario",
        "monetary fund", "ucits mmf", "standard money market",
        "short term money market", "low volatility money market",
        "fondsmonétaire", "geldmarktfonds",
        # DDF — señales de mercado monetario en formato PRIIPs
        "instrumentos del mercado monetario",
        "vencimiento medio ponderado",
        "activos en instrumentos del mercado",
        "mercados monetarios",
        "money market instruments",
        "weighted average maturity",
    ]):
        return "Monetario"

    # ── A partir de aquí usar ventana objetivo ───────────────────────────────

    # Retorno absoluto con benchmark monetario → Alternativo
    has_ar = any(k in w for k in [
        "absolute return", "retorno absoluto", "rendimiento positivo independientemente",
        "positive return regardless", "en cualquier entorno de mercado",
        "market neutral", "long/short", "long short",
    ])
    has_cash_bench = any(k in w for k in [
        "€str", "estr", "eonia", "sonia", "sofr", "overnight",
        "tasa libre de riesgo",
    ])
    if has_ar and has_cash_bench:
        return "Alternativo"

    # ── Señales de presencia (no dominantes) — declaradas antes de usarlas ──
    has_equity = any(k in w for k in [
        "equity securities", "acciones y otros valores",
        "invierte en acciones", "invests in equities",
        "acciones ordinarias",
        "primarily in equities", "mainly in equities",
        # Señales DDF genéricas — frases que indican inversión PRIMARIA en RV
        "acciones de empresas", "acciones emitidas",
        "valores de renta variable", "cartera de acciones",
        "mediante la inversión en acciones",
        "invierte en valores de renta variable",
        "acciones y otros valores de renta variable",
        "en acciones y otros", "shares of companies",
        "company shares", "common shares",
        "reproduce", "replica la rentabilidad",  # fondos indexados equity
        "seguimiento del índice",
        "fondo de renta variable",               # mención explícita como tipo de fondo
        "invertir en renta variable",             # intención de inversión en RV
        "inversiones de renta variable",          # cartera de RV
    ])
    # Nota: "renta variable" sola se eliminó — demasiado amplia con ventana [500:4500]
    # Aparece en textos de bonos/mixtos como mención incidental de activos alternativos
    # En su lugar: "renta variable de" (seguido de "empresas", geografía, etc.)
    # distingue "inversión EN renta variable" de "renta variable Y bonos"
    has_equity = has_equity or any(k in w for k in [
        "renta variable de",             # "RV de empresas cotizadas", "RV de todo el mundo"
        "renta variable global",          # "global equity"
        "acciones de compañías",
        "acciones de sociedades",
    ])
    has_bonds = any(k in w for k in [
        "valores de renta fija", "fixed income securities",
        "invierte en bonos", "inverts in bonds",
        "renta fija", "invierte principalmente en bonos",
        "primarily in bonds", "debt securities",
        "invierte al menos", "invierte en valores de deuda",
        # Señales DDF genéricas adicionales
        "títulos de deuda",
        # "instrumentos de deuda" eliminado — aparece en equity funds en contexto
        # de warrants/pagarés ("instrumentos de deuda vinculados a RV")
        "deuda soberana", "deuda corporativa",
        "bonos corporativos", "bonos soberanos",
        "bonos y otros", "bonos (incluidos",
        "valores de deuda", "obligaciones",
        "renta fija y", "en bonos y",
        "bond securities", "fixed rate", "floating rate notes",
        "high yield bonds", "investment grade",
        "grado de inversión", "calificación crediticia",
    ])

    # RF dominante (declaración explícita de objetivo)
    bond_dominant = any(k in w for k in [
        "primarily in bonds", "mainly in bonds", "principally in bonds",
        "invierte principalmente en bonos", "invierte en bonos",
        "fixed income securities", "fixed income fund",
        "renta fija", "fixed income", "bond fund", "fondo de bonos",
        "invierte en valores de renta fija",
        "invierte principalmente en instrumentos de renta fija",
        "debt securities", "debt fund", "inverts in debt securities",
        "valores de renta fija",
        # Señales DDF adicionales — frases de política de inversión
        "títulos de deuda",
        # "instrumentos de deuda" eliminado — aparece en equity funds en contexto
        # de warrants/pagarés ("instrumentos de deuda vinculados a RV")
        "deuda soberana", "deuda corporativa",
        "bonos corporativos", "bonos y otros títulos",
        "principalment en bonos", "principalement en obligat",
        "grado de inversión", "investment grade bonds",
        "bonos de alto rendimiento",
        "activos principales: bonos", "principales activos: bonos",
        "principales activos negociados: bonos",
    ])

    # RV dominante (declaración explícita de objetivo)
    eq_dominant = any(k in w for k in [
        "primarily in equities", "mainly in equities", "principally in equities",
        "invierte principalmente en acciones", "invest in shares",
        "invierte en acciones", "equity securities",
        "acciones y otros valores de renta variable",
        "invests mainly in shares", "fondo de renta variable",
        "acciones y otros valores",
        # Señales DDF adicionales
        "acciones de empresas", "acciones emitidas por",
        "en acciones de", "valores de renta variable",
        "principalment en acciones", "principalmente en acciones",
        "acciones ordinarias y otros",
        "invirtiendo en acciones",
        # Fondos indexados equity (passive)
        "reproduce la rentabilidad del", "replica la rentabilidad del",
        "reproduce (con un error", "réplica del índice",
        "seguimiento del índice de renta variable",
        "inversión pasiva en acciones",
    ]) or _has_equity_in_header or _ocr_equity

    # RV dominante sin RF → Renta Variable
    if eq_dominant and not bond_dominant:
        return "Renta Variable"

    # RF dominante sin equity en absoluto → pendiente corto/flexible
    if bond_dominant and not eq_dominant and not has_equity:
        return "_RF_pending"

    # RF dominante + equity presente (mención incidental en fondos RV/Mixtos) →
    # Devolver None: la Capa 2 (nombre del fondo) lo resolverá correctamente
    if bond_dominant and not eq_dominant and has_equity:
        return None


    # Mixto explícito
    if any(k in w for k in [
        "tanto acciones como bonos", "both equities and bonds",
        "equities and bonds", "stocks and bonds", "acciones y bonos",
        "renta variable y renta fija", "multiactivo", "multi-asset",
        "asset allocation", "asignación de activos", "múltiples clases de activos",
        "varias clases de activos", "multiple asset class",
        # Señales DDF adicionales
        "renta variable y de bonos", "renta variable y bonos",
        "amplia gama de clases de activos",
        "diversas clases de activos", "diferentes clases de activos",
        "acciones y bonos y", "renta variable, renta fija",
        "volatilidad del 3", "volatilidad del 5", "volatilidad del 7",
        "volatilidad comprendida", "rango de volatilidad",
        "protección parcial permanente",  # Amundi Protect 90
        "valor liquidativo mínimo",        # capital protection
        "floor de capital", "capital floor",
    ]):
        return "Mixtos"

    if has_equity and has_bonds:
        if has_ar:
            return "Alternativo"
        return "Mixtos"

    if has_bonds and not has_equity:
        return "_RF_pending"
    if has_equity and not has_bonds:
        return "Renta Variable"

    # ── Multi-asset en texto completo (DDF con layout de dos columnas) ─────
    # Cubre fondos cuyo objetivo aparece a partir de pos 3500-5000 (OCR de
    # columnas dobles desplaza el texto fuera de la ventana estándar).
    # Solo frases compuestas inequívocas — no términos genéricos.
    if any(k in t for k in [
        "invierte en títulos de renta variable y en instrumentos de",
        "renta variable y en instrumentos de deuda",
        "invierte en renta variable y en renta fija",
        "equity and fixed income",
    ]):
        return "Mixtos"

    # SRRI como árbitro (ventana completa)
    m = re.search(r"\b([1-7])\s*/\s*7\b", t)
    srri = int(m.group(1)) if m else None
    if srri == 1:
        return "Monetario"
    if srri is not None and srri >= 5:
        return "Renta Variable"
    if srri == 2:
        return "_RF_pending"

    return None


def resolve_rf_subtype(name_l: str, kiid_text: str) -> str:
    """
    Decide si un fondo marcado como '_RF_pending' es RF_Corto o RF_Flexible.
    Devuelve claves INTERNAS ('RF_Corto', 'RF_Flexible') para que
    _NATURE_CANONICAL pueda mapearlas correctamente.
    Fuente única para restantes.py y detect_nature_from_kiid.
    """
    t = kiid_text.lower() if kiid_text else ""
    _obj_start, _obj_end = _get_obj_bounds(kiid_text or "")
    w = _extract_window(t, _obj_start, _obj_end)

    # Inflation-linked bonds: siempre RF_Flexible (indexados a inflación no son corto plazo)
    if any(k in w for k in [
        "inflation-linked", "bonos indexados a la inflación",
        "inflation linked bond", "tips ",
        "ligado a la inflación", "linked to inflation",
        "replicar la rentabilidad del", "replicación de la rentabilidad",
    ]) and any(k in w for k in [
        "bonos", "deuda", "renta fija", "bond", "fixed income", "índice", "index",
    ]):
        return "RF_Flexible"

    # Señales explícitas de corto plazo en el objetivo
    if any(k in w for k in [
        "duración inferior", "duration below", "duration less than",
        "duration of less", "short duration", "ultra short", "ultrashort",
        "baja duración", "low duration", "corto plazo", "court terme",
        "0 a 2 año", "0 a 3 año", "0 to 2 year", "0 to 3 year",
        "1 a 3 año", "1 to 3 year", "menos de 3 años", "below 3 year",
        "menos de 2 años", "below 2 year", "short-term bond",
        "target maturity", "vencimiento fijo", "fixed maturity",
        "fecha de vencimient", "horizon 202", "credit 202", "bond 202",
    ]):
        return "RF_Corto"

    # Señales en nombre
    if _name_match(name_l, NAME_SIGNALS_RF_CORTO):
        return "RF_Corto"

    # SRRI muy bajo
    m = re.search(r"\b([1-7])\s*/\s*7\b", t)
    if m and int(m.group(1)) == 1:
        return "RF_Corto"

    return "RF_Flexible"


# ============================================================
# detect_kiid_attributes — enriquecimiento completo desde KIID
# ============================================================

def detect_type_from_kiid(kiid_text: str, fund_nature: str) -> Optional[str]:
    """
    Infiere Type desde el texto KIID (ventana objetivo).
    Solo cuando el bloque primario no ha podido asignarlo por nombre.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    if fund_nature == "Renta Fija Flexible":
        if any(k in w for k in ["high yield","alto rendimiento","bono de alto rendimiento"]):
            return "High Yield"
        # Emergentes: exigir señal dominante, no mención incidental
        if any(k in w for k in [
            "invierte principalmente en mercados emergentes",
            "primarily in emerging markets",
            "deuda de mercados emergentes como objetivo",
            "emerging market debt fund",
            "emerging market bond fund",
            "mercados emergentes como objetivo principal",
        ]):
            return "Emergentes"
        # Señal moderada: inversión significativa aunque no exclusiva en EM
        if any(k in w for k in [
            "emerging market debt","deuda emergente",
            "bonos de mercados emergentes",
            "considerablemente en los mercados emergentes",
            "significantly in emerging markets",
            "invertir en mercados emergentes",
        ]):
            return "Emergentes"
        if any(k in w for k in ["inflación","inflation-linked","vinculado a la inflación","tips"]):
            return "Inflación"
        if any(k in w for k in [
            "invierte principalmente en covered bond",
            "primarily in covered bond",
            "bonos garantizados como objetivo",
            "pfandbrief","covered bond fund",
            "fondo de covered bond",
        ]):
            return "Covered Bond"
        if any(k in w for k in ["convertible bond","bono convertible","obligaciones convertibles"]):
            return "Convertible"
        if any(k in w for k in [
            "invierte principalmente en bonos de gobierno",
            "primarily in government bond",
            "invierte en deuda pública","deuda del estado",
            "bonos soberanos","sovereign bond fund",
            "government bond fund","fondo de bonos gubernamentales",
        ]):
            return "Gobierno"
        # Corporativo: señal explícita de objetivo, no mención incidental
        if any(k in w for k in [
            "invierte principalmente en bonos corporativos",
            "primarily in corporate bond",
            "corporate bond fund","fondo de crédito corporativo",
            "invierte en crédito corporativo","corporate credit fund",
        ]):
            return "Corporativo"
        # Señal moderada: menciona IG o HY en contexto de política de inversión
        if any(k in w for k in [
            "cartera de bonos corporativos","bonos corporativos investment grade",
            "crédito con grado de inversión","investment grade corporate",
            "grado de inversión como objetivo",
        ]):
            return "Corporativo"
        if any(k in w for k in ["target maturity","vencimiento fijo","fixed maturity",
                                  "fecha objetivo"]):
            return "Target Maturity"
        if any(k in w for k in ["total return","rentabilidad total","unconstrained",
                                  "multi-sector","multisector"]):
            return "Unconstrained"

    elif fund_nature == "Renta Fija Corto Plazo":
        if any(k in w for k in ["floating rate","tipo flotante","bonos flotantes","frn"]):
            return "Floating Rate"
        if any(k in w for k in ["covered bond","bonos garantizados","pfandbrief"]):
            return "Covered Bond"
        if any(k in w for k in ["gobierno","government","treasury","sovereign","tesoro"]):
            return "Gobierno CP"
        if any(k in w for k in ["corporate","corporativo","crédito","credit"]):
            return "Crédito CP"

    elif fund_nature == "Monetario":
        if any(k in w for k in ["cnav","constant nav","valor liquidativo constante"]):
            return "CNAV"
        if any(k in w for k in ["lvnav","baja volatilidad del valor"]):
            return "LVNAV"
        if any(k in w for k in ["vnav","variable net asset"]):
            return "VNAV"
        if any(k in w for k in ["enhanced cash","monetario plus","rendimiento adicional"]):
            return "Enhanced Cash"

    elif fund_nature == "Renta Variable":
        if any(k in w for k in ["replica","tracks","sigue el índice","seguimiento del índice",
                                  "index fund","fondo índice"]):
            return "Indexado"
        if any(k in w for k in ["smart beta","factor investing","quality factor","value factor"]):
            return "Smart Beta"

    elif fund_nature == "Mixto":
        if any(k in w for k in ["target volatility","volatilidad objetivo"]):
            return "Target Volatility"
        if any(k in w for k in ["tactical","táctica","gestión táctica"]):
            return "Tactical"
        if any(k in w for k in ["lifecycle","ciclo de vida","target date"]):
            return "Lifecycle"
        return "Allocation"  # default para mixtos

    elif fund_nature == "Alternativo":
        if any(k in w for k in ["absolute return","retorno absoluto"]):
            return "Absolute Return"
        if any(k in w for k in ["long/short","long short","posiciones largas y cortas"]):
            return "Long/Short"
        if any(k in w for k in ["market neutral","neutral al mercado"]):
            return "Market Neutral"
        if any(k in w for k in ["systematic","sistemático","cta","managed futures"]):
            return "Sistemático/CTA"
        if any(k in w for k in ["commodities","materias primas","commodity"]):
            return "Commodities"
        if any(k in w for k in ["real assets","activos reales","real estate","inmobiliario"]):
            return "Real Assets"

    return None


def detect_style_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta Style_Profile desde la ventana objetivo del KIID.
    Complementa detect_style_profile (que solo usa el nombre).
    Usa señales explícitas de política de inversión, no menciones incidentales.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    if any(k in w for k in [
        "baja volatilidad","low volatility","volatilidad reducida",
        "preservación del capital","capital preservation",
        "minimum variance","minimum volatility","mínima varianza",
        "gestión del riesgo absoluto","control de volatilidad",
    ]):
        return "Low Volatility"

    if any(k in w for k in [
        "generación de rentas","income distribution","generar rentas",
        "reparte dividendos","distribuye dividendos","ingresos regulares",
        "rendimientos periódicos","distribución periódica",
        "distributing shares","clase de distribución",
        "objetivo de rentas","income objective","orientado a rentas",
        "income fund","income oriented",
    ]):
        return "Income"

    if any(k in w for k in [
        "valor intrínseco","infravaloradas","infravalorados",
        "value investing","estrategia value","análisis fundamental de valor",
        "acciones de valor","cotización inferior a su valor",
    ]):
        return "Value"

    if any(k in w for k in [
        "crecimiento de beneficios","empresas de alto crecimiento",
        "potencial de crecimiento","crecimiento sostenido",
        "growth investing","growth stocks","growth equity",
        "crecimiento del capital a largo plazo",
    ]):
        return "Growth"

    if any(k in w for k in [
        "momentum","seguimiento de tendencias","trend following",
        "impulso de precios","estrategia de momentum",
    ]):
        return "Momentum"

    if any(k in w for k in [
        "risk control","riesgo controlado","control de riesgo",
        "volatility target","objetivo de volatilidad",
        "paridad de riesgo","risk parity","volatilidad objetivo",
    ]):
        return "Risk Control"

    return None


def detect_geography_from_kiid(kiid_text: str) -> Optional[str]:
    """
    Detecta Geography desde la ventana objetivo del KIID.
    Solo se usa cuando detect_geography (por nombre) devuelve None.

    Usa señales EXPLÍCITAS de objetivo de inversión (no menciones incidentales).
    Orden: específicas primero, globales al final.
    """
    if not kiid_text:
        return None
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)

    # Señales explícitas de objetivo — frases que declaran la geo principal
    _GEO_OBJ_PATTERNS = [
        # Japón
        (["invierte principalmente en japón","invierte en japón",
          "japanese equities","renta variable japonesa",
          "japanese government","mercado japonés"], "Japón"),
        # China
        (["invierte principalmente en china","chinese equities",
          "renta variable china","mercado chino",
          "a-shares","gran china"], "China"),
        # Asia
        (["asia-pacífico","asia pacific","invierte en asia",
          "mercados asiáticos","asian equities"], "Asia"),
        # India
        (["invierte en india","mercado indio","indian equities"], "India"),
        # Latinoamérica
        (["latinoamérica","latin america","invierte en brasil"], "Latinoamérica"),
        # EEUU — ANTES que Emergentes para evitar falso positivo
        (["invierte principalmente en estados unidos",
          "invierte en estados unidos","us equities",
          "renta variable estadounidense","mercado estadounidense",
          "bonos gubernamentales y corporativos de estados unidos",
          "valores de estados unidos","norteamerica",
          # Señales indirectas fiables (benchmark, índice)
          "bloomberg us aggregate","s&p 500","russell 1000",
          "russell 2000","dow jones","nasdaq",
          # Mención directa sin prefijo
          "estados unidos"], "EEUU"),
        # Europa — ANTES que Emergentes
        (["invierte principalmente en europa","invierte en europa",
          "zona euro","eurozona","valores europeos",
          "renta variable europea","mercado europeo",
          "european equities","european bonds"], "Europa"),
        # Global — ANTES que Emergentes: fondos globales mencionan EM incidentalmente
        (["invierte a nivel mundial","invierte en todo el mundo",
          "mercados de todo el mundo","globally diversified",
          "diversificación global","cartera global",
          # Índices de referencia globales como proxy fiable
          "jp morgan global government bond","global government bond index",
          "world government bond","bloomberg global aggregate",
          "msci world","msci acwi","ftse world",
          "global bond fund","global equity fund"], "Global"),
        # Emergentes — señal dominante requerida
        (["invierte principalmente en mercados emergentes",
          "mercados emergentes como objetivo principal",
          "emerging market debt","emerging market equities",
          "deuda de mercados emergentes",
          "renta variable de mercados emergentes"], "Emergentes"),
    ]

    for signals, geo in _GEO_OBJ_PATTERNS:
        if any(s in w for s in signals):
            return geo

    return None


def detect_esg_from_kiid(kiid_text: str) -> int:
    """
    Detecta política ESG desde el texto KIID.
    Complementa detect_is_esg (que solo usa el nombre).
    Detecta referencias Art. 8/9 SFDR y criterios ASG explícitos.
    """
    if not kiid_text:
        return 0
    _obj_start, _obj_end = _get_obj_bounds(kiid_text)
    w = _extract_window(kiid_text.lower(), _obj_start, _obj_end)
    if any(k in w for k in [
        "artículo 8", "artículo 9", "article 8", "article 9",
        "sfdr", "reglamento de divulgación",
        "características medioambientales y sociales",
        "environmental and social characteristics",
        "sustainable investment", "inversión sostenible",
        "promueve características medioambientales",
        "integra el riesgo y los factores esg",
        "criterios ambientales, sociales y de gobernanza",
        "esg criteria", "criterios esg",
        "objetivo de inversión sostenible",
    ]):
        return 1
    return 0


def detect_ongoing_charge_from_kiid(kiid_text: str) -> Optional[float]:
    """
    Extrae Ongoing_Charge desde la ventana de costes del KIID (9000-14000).

    NOTA: La posición de la sección de costes varía según la gestora y el
    formato del KIID (UCITS vs PRIIPs). Validación con datos reales muestra
    que la ventana fija 9000-14000 captura con frecuencia la sección de
    riesgos (no costes), generando valores incorrectos.

    Esta función queda reservada para uso futuro cuando se implemente
    detección dinámica de la posición de la sección de costes.
    El parser principal (kiid_parser.py) sigue siendo la fuente de OC.
    """
    return None  # Deshabilitado — ver docstring


def detect_kiid_attributes(
    kiid_text: str,
    fund_nature: str,
    current_attrs: Optional[dict] = None,
) -> dict:
    """
    Extrae atributos clasificatorios desde el texto KIID usando las
    ventanas correctas. Solo rellena atributos que no han sido asignados
    por el bloque (principio: bloque tiene precedencia).

    Parametros:
        kiid_text:     texto completo del KIID
        fund_nature:   naturaleza ya asignada al fondo
        current_attrs: dict con atributos ya asignados por el bloque
                       (si se pasa, solo rellena los NULL/None)

    Devuelve dict con: Type, Style_Profile, Geography, Is_ESG,
                       Exposure_Bias
    Solo incluye valores detectados — no sobreescribe nada.
    """
    cur = current_attrs or {}
    result = {}

    # Type
    if not cur.get("Type") or cur.get("Type") == fund_nature:
        t = detect_type_from_kiid(kiid_text, fund_nature)
        if t:
            result["Type"] = t

    # Style_Profile
    if not cur.get("Style_Profile") or cur.get("Style_Profile") == "Defensivo":
        s = detect_style_from_kiid(kiid_text)
        if s:
            result["Style_Profile"] = s

    # Geography
    if not cur.get("Geography"):
        g = detect_geography_from_kiid(kiid_text)
        if g:
            result["Geography"] = g

    # Is_ESG — combina nombre (detect_is_esg ya en el bloque) y KIID
    esg_kiid = detect_esg_from_kiid(kiid_text)
    if esg_kiid:
        result["Is_ESG"] = 1

    # Nota: Ongoing_Charge NO se incluye aqui.
    # Se extrae en pipeline.py directamente via detect_ongoing_charge_from_kiid()
    # porque es un campo del parser (Grupo 4), no de clasificacion (Grupo 2).

    return result


# ============================================================
# Resto de funciones universales (sin cambios respecto a v2)
# ============================================================

def detect_geography(name_l: str) -> Optional[str]:
    """Detecta geografía desde el nombre del fondo (en minúsculas)."""
    if any(k in name_l for k in ["japan","japanese","japon"]):
        return "Japón"
    if "jpy" in name_l:
        return "Japón"
    if any(k in name_l for k in ["china","chinese","a-shares","greater china","gran china","hong kong"]):
        return "China"
    if any(k in name_l for k in ["asia pacific","asia-pacific","apac","asean","pacific"]):
        return "Asia"
    if any(k in name_l for k in ["asia","asian","asia ex"]):
        return "Asia"
    if any(k in name_l for k in ["india","indian"]):
        return "India"
    if any(k in name_l for k in ["brazil","brasil","latin","latam"]):
        return "Latinoamérica"
    if any(k in name_l for k in ["mena","middle east"]):
        return "Emergentes"
    if any(k in name_l for k in ["emerging","emergentes","emergent","em mkt","emerg mkt",
                                   "emerg ","emrg","emer mkt","emer ","frontier"]):
        return "Emergentes"
    if any(k in name_l for k in ["us ","usa","u.s.","united states","america","american",
                                   "us eq","us sm","us sel","treasury","t-bill","us govt",
                                   "us dollar","us money"]):
        return "EEUU"
    if " usd " in name_l:
        return "EEUU"
    if any(k in name_l for k in [" uk ","uk eq","uk inc","uk sit","uk sc","uk ag",
                                   "united kingdom","british","britain"," gbp ","gbp ac",
                                   "gbp in","gbphdg","sterling"]):
        return "Europa"
    if any(k in name_l for k in ["swiss","switzerland"," chf ","chf ac","chf p ","chfhdg"]):
        return "Europa"
    if any(k in name_l for k in ["russia","osteuropa","eastern euro","east europ"]):
        return "Europa del Este"
    if any(k in name_l for k in ["europe","european","euro "," euro","euroland","eurozone",
                                   "europ","europa","euroz","emu","deutsch","germany",
                                   "italia","italian","iberia","nordic","france","french"]):
        return "Europa"
    if any(k in name_l for k in ["global","glob ","globl"," glb "," gbl ","glbl","glbal",
                                   " gl ","world","wrld","wld ","international","intl",
                                   "worldwide","multi-region","multiregion"]):
        return "Global"
    if "usdh" in name_l:
        return "Global"
    if " eur " in name_l:
        return "Europa"
    return None


THEMATIC_MAP: dict = {
    "technology": "Technology", "tech": "Technology",
    "smart ind tec": "Technology",
    "artificial intelligence": "Artificial Intelligence",
    "artificial intelligenc": "Artificial Intelligence",
    " ai ": "Artificial Intelligence",
    "digital": "Digital", "robotics": "Robotics", "robotech": "Robotics",
    "healthcare": "Healthcare", "health": "Healthcare", "wellcare": "Healthcare",
    "biotec": "Biotechnology", "biotech": "Biotechnology",
    "climate": "Climate / Clean Energy", "clean energy": "Climate / Clean Energy",
    "renewable": "Climate / Clean Energy",
    "water": "Water", "pictet water": "Water",
    "energy": "Energy",
    "real estate": "Real Estate", "real estat": "Real Estate", "property": "Real Estate",
    "silver age": "Silver Economy", "silverplus": "Silver Economy",
    "insurance": "Insurance",
    "global brands": "Consumer Brands", "glob brands": "Consumer Brands",
    "financial": "Financials", "financials": "Financials",
    "mining": "Mining", "gold": "Gold",
    "infrastructure": "Infrastructure", "infraestructura": "Infrastructure",
}


def detect_theme(name_l: str) -> Optional[str]:
    """Detecta temática solo desde el nombre del fondo (canónico v2)."""
    for keyword, theme in THEMATIC_MAP.items():
        if keyword in name_l:
            return theme
    return None


_ESG_NAME_KEYWORDS = [
    "esg","sustainable","sustainability","sri","responsible",
    "green bond","climate aware","impact","paris aligned",
    "low carbon","carbon","socially","net zero","transition",
]


def detect_is_esg(fund_name: str) -> int:
    """Detecta política ESG desde el nombre del fondo."""
    if not fund_name or not isinstance(fund_name, str):
        return 0
    name_l = fund_name.lower()
    return 1 if any(k in name_l for k in _ESG_NAME_KEYWORDS) else 0


def detect_style_profile(name_l: str) -> Optional[str]:
    """Detecta estilo de gestión desde el nombre del fondo."""
    if any(k in name_l for k in ["low vol","low volatility","minimum volatility",
                                   "minimum vol","min vol","min volatil",
                                   "low risk","capital preservation"]):
        return "Low Volatility"
    if any(k in name_l for k in ["income","dividend","dividende","dividends",
                                   "rend","rendement","high yield"]):
        return "Income"
    if "quality" in name_l:
        return "Quality"
    if any(k in name_l for k in ["growth","wachstum","crecim","crecimiento"]):
        return "Growth"
    if "value" in name_l and "relative value" not in name_l:
        return "Value"
    if any(k in name_l for k in ["momentum","trend","trend follow"]):
        return "Momentum"
    if any(k in name_l for k in ["risk control","risk managed","risk parity",
                                   "risk target","volatility target"]):
        return "Risk Control"
    return None


def detect_exposure_bias(name_l: str, fund_nature: Optional[str] = None) -> Optional[str]:
    """Detecta sesgo estructural de cartera. NULL obligatorio en Monetario y Mixto."""
    if fund_nature in ("Monetario","Mixto"):
        return None
    if any(k in name_l for k in ["barrier","autocall","knock-in"]):
        return "Barrier Risk"
    if any(k in name_l for k in ["commodit","commodity","gold","precious metal",
                                   "energy","oil","mining","copper"]):
        return "Commodity Bias"
    if any(k in name_l for k in ["real estate","property","reit","epra"]):
        return "Real Estate Bias"
    if any(k in name_l for k in ["absolute return","total return","market neutral",
                                   "long short","long/short"]):
        return "Absolute Return Bias"
    if any(k in name_l for k in ["low vol","minimum volatility","min vol","low risk"]):
        return "Low Volatility Bias"
    if any(k in name_l for k in ["income","dividend","dividende"]):
        return "Income Bias"
    if any(k in name_l for k in ["credit","crédito","high yield","hy ","corporate",
                                   "corp bond","opportunistic"]):
        return "Credit Bias"
    if any(k in name_l for k in ["float","floating rate","frn","variable rate"]):
        return "Rate Reset Bias"
    if any(k in name_l for k in ["liquid","liquidity","money market","cash"]):
        return "Liquidity Bias"
    if fund_nature in ("Renta Fija Corto Plazo","Renta Fija Flexible"):
        return "Duration Bias"
    return None


def detect_strategy(
    replication_method: Optional[str],
    subtype: Optional[str],
    name_l: str = "",
) -> Optional[str]:
    """Consolida la estrategia de gestión."""
    sub_l = (subtype or "").lower()
    rep_l = (replication_method or "").lower()
    if any(k in sub_l for k in ["fondo indexado","etf","index fund"]):
        return "Indexado"
    if any(k in name_l for k in ["etf","index fund","tracker"]):
        return "Indexado"
    if any(k in sub_l for k in ["systematic","cta","quant"]):
        return "Sistemático"
    if any(k in name_l for k in ["systematic","quant ","cta ","managed future"]):
        return "Sistemático"
    if any(k in name_l for k in ["smart beta","factor","multi-factor","multifactor",
                                   "quality factor","value factor"]):
        return "Factor"
    if rep_l == "passive":
        return "Pasivo"
    if any(k in name_l for k in ["passive","passiv","replica"]):
        return "Pasivo"
    if rep_l == "active":
        return "Activo"
    return None


def detect_benchmark_type(
    benchmark_declared: Optional[str],
    replication_method: Optional[str] = None,
) -> Optional[str]:
    """Infiere el tipo de relación con el benchmark."""
    if benchmark_declared == "NO_BENCHMARK":
        return "NO_BENCHMARK"
    if not benchmark_declared:
        return None
    rep_l = (replication_method or "").upper()
    if rep_l == "PASSIVE":
        return "TARGET_INDEX"
    bench_l = benchmark_declared.lower()
    if any(k in bench_l for k in ["replica","track","tracks","replicat"]):
        return "TARGET_INDEX"
    return "REFERENCE_INDEX"


def detect_profile_from_srri(srri: Optional[int]) -> Optional[str]:
    """Deriva Profile desde SRRI con precedencia absoluta."""
    if srri is None:
        return None
    if srri <= 2:
        return "Conservador"
    if srri <= 4:
        return "Moderado"
    return "Dinámico"
