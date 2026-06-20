# -*- coding: utf-8 -*-
"""
core/kiid_parser.py
Parser KIID determinista  — v28

Cambios v28 (2026-05-08) -- BL-DLA-2: fix causa raiz exit_fee_null (3 bugs)

  BL-DLA-2  _EXIT_FEE_ZERO_RE ampliado con 3 nuevas ramas (diagnostico sobre
            534 textos reales de fondos exit_fee_null):

            Bug 1 (355 fondos FR): patron buscaba 'no cobr[amos|a] comision de
            salida' pero texto real es 'No cobramos UNA comision de salida por
            este producto'. Articulo 'una' entre cobramos y comision rompia
            el match. Fix: (?:una\s+)? entre cobr* y comision.

            Bug 2 (65 fondos IE/AXA): 'Nosotros no facturamos el coste de
            salida de este producto.' El equivalente para entry fee existia
            (_EF_AXA_NO_FACTURA) pero no para exit fee.
            Fix: nueva rama 'nosotros no facturamos el coste de salida'.

            Bug 3 (4 fondos FR): 'Coste de salida' (singular) no matcheaba
            el trigger (costes? cubre plural pero el singular sin 's' al
            final necesitaba la adicion del trigger al patron).
            Fix: costes? ya cubre 'coste' y 'costes' (la 's' es opcional).
            En realidad el trigger ya era correcto -- estos 4 se resuelven
            por la rama 'no cobramos coste de salida' del Bug 1.

            Impacto esperado: exit_fee_null baja de 534 a ~80-100.
            Control SQL post-ejecucion:
              SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
              -- Objetivo: < 100
              SELECT Fee_Known_Flag, COUNT(*) FROM fund_master
              GROUP BY Fee_Known_Flag ORDER BY 2 DESC;

Cambios v27 (2026-05-06) -- BL-DLA-2: patron tabular PRIIPs con salto de linea

  BL-DLA-2  _XF_TABLA_PRIIPS_RE: nuevo patron (prioridad 13 en _detect_exit_fee).
            Causa raiz de 510 fondos LU con Exit_Fee_Pct NULL: _EXIT_FEE_RE usa
            separador no-newline que no puede cruzar el salto de linea entre
            trigger y valor en layout PRIIPs tabulado estandar.
            Separador acotado a 80 chars con lookahead negativo (Ninguna/no cobr),
            restriccion de cruce de filas, y prefijos opcionales (Hasta/EUR).
            Impacto esperado: Exit_Fee_Pct NULL baja de ~530 a ~20-30.
            Control SQL post-ejecucion:
              SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
              -- Objetivo: < 30 (desde ~530 actual)

Cambios v26.1 (2026-04-25) — BL-55/2: endurecimiento ventana acotada

  BL-55/2  _infer_exit_fee_from_structure() reescrita: la versión inicial v26
           verificaba ausencia de exit keywords en TODO el texto del KIID;
           cualquier mención incidental (índice, glosario, ejemplo, FAQ) bloqueaba
           la inferencia. Resultado real ciclo 25/04/2026: solo 3 fondos
           inferidos sobre 676 candidatos (impacto neto Exit_Fee NULL: -3).

           Solución: la verificación de ausencia se acota a una VENTANA de
           ±1500 chars alrededor del primer match de cost section. Esto evita
           falsos negativos por menciones incidentales fuera del contexto
           estructural relevante. _COST_SECTION_KEYWORDS ampliado con variantes
           adicionales (estructura de costes, fund charges, ongoing charges, etc).
           Eliminada la regla '\\.{3,}' (puntos suspensivos) que era demasiado
           agresiva: KIIDs reales contienen "..." en encabezados, "etc.",
           listas truncadas en HTML→texto sin que esto implique OCR degradado.

           Impacto esperado: cobertura de inferencia estructural ~50–70% sobre
           los 670 candidatos restantes (Exit_Fee_Pct NULL bajaría a ~250–340).

Cambios v26 (2026-04-25) — BL-55: Exit_Fee_Pct=0.00 para declaraciones implícitas

  BL-55  Nuevo helper _infer_exit_fee_from_structure(text) → Optional[float]:
         Formaliza Exit_Fee_Pct=0.0 cuando el KIID contiene la sección de costes
         estructurada claramente identificada (presencia de keyword "Composición
         de costes" / "Composition of charges") pero NO menciona ninguna
         palabra-clave de salida (salida, reembolso, exit, redemption).
         Principio: ausencia de mención en sección estructurada ≡ no aplica.
         Restricciones de seguridad:
           - Texto < 500 chars → OCR degradado → no inferir.
           - Texto contiene marcadores de truncado ('[truncado]', '...') → no inferir.
           - Raw_KIID_Text=NULL → no inferir.
         Nuevo valor Fee_Known_Flag: 'EXIT_INFERRED_ZERO' distingue el cero
         inferido estructuralmente del cero explícito ('ZERO_CONFIRMED') y del
         genuinamente ausente ('NOT_FOUND'). No es breaking change (columna TEXT).

         Nuevos patrones explícitos añadidos a _detect_exit_fee() (v26):
           Prioridad 9:  ES declaración negativa directa:
             "sin comisión/gastos de salida/reembolso"
             "no hay/inexistentes gastos de salida"
             "comisión de salida: 0 / cero / ninguna / nil / n/a / — / –"
           Prioridad 10: EN declaración negativa directa:
             "no/nil/none exit/redemption charge/fee/load"
             "exit/redemption charge: 0.00% / none / nil / n/a / —"
           Prioridad 11: tabular fusionado adicional:
             "costesdesalida: ninguno/ninguna"
             "exitcharges: 0.00%"
           Prioridad 12: cero estructural (_infer_exit_fee_from_structure)

         Paso 10d del parser principal actualizado:
           - Si _detect_exit_fee() devuelve 0.0 por prioridades 1-11 →
             Fee_Known_Flag marcado como 'EXIT_EXPLICIT_ZERO'
             (distingue del ZERO_CONFIRMED de entry fee).
           - Si _infer_exit_fee_from_structure() devuelve 0.0 →
             Exit_Fee_Pct=0.0 + Fee_Known_Flag='EXIT_INFERRED_ZERO'.
           - Sin detección → Exit_Fee_Pct=NULL (sin cambio en Flag existente).

Cambios v25 (2026-04-19) — BL-51A ciclo 2: fix root cause del resultado insuficiente de v24

  BL-51A/2  Root cause: separador [^\r\n]{0,300} GREEDY en _ENTRY_FEE_RE y _EXIT_FEE_RE
            consumía los dígitos antes del %, dejando que el grupo capturador solo
            atrapara el último "0" de "3,00%". Fix:
            (1) [^\r\n]{0,300}? no-greedy en ambos patrones base.
            (2) ([\d]+(?:[,.][\d]+)?) decimal opcional — cubre "5%" y "5,00%".
            (3) Nuevos triggers _ENTRY_FEE_RE (ES): "comisión de entrada",
                "comisión inicial", "gastos de entrada", "cargo inicial",
                "cargo máximo de entrada", "derecho de suscripción".
            (4) Nuevos triggers _ENTRY_FEE_RE (EN): "purchase fee",
                "upfront charge/fee", "sales load".
            (5) Nuevos triggers _EXIT_FEE_RE: "derecho de reembolso",
                "redemption load", "back-end load", "deferred sales charge".
            (6) _ENTRY_FEE_ZERO_RE: triggers "comisión inicial" y "cargo inicial".
            Test: 35/35 OK.

Cambios v24 (2026-04-19) — BL-51 Problema A: extensión de patrones de comisiones

  BL-51A  _detect_entry_fee(): 6 nuevos patrones de extracción para fondos
          con comisión de entrada no capturada por patrones v23.

          Nuevos patrones de ZERO (prioridad alta):
          - _EF_NO_HAY_GASTOS_RE: "no hay gastos de entrada" — Fidelity/BNP.
          - _EF_SIN_CARGO_RE: "sin cargo de entrada" / "sin cargo inicial"
            — Vanguard, Robeco y similares.
          - _EF_NO_FRONT_LOAD_RE: "no front-end load" / "no sales charge"
            / "no initial charge" — fondos con KIID en inglés (Language=EN).

          Nuevos patrones de PORCENTAJE (completitud):
          - _EF_FRONT_LOAD_EN_RE: "front-end load [of] X%" / "sales charge X%"
            / "initial charge [of up to] X%" — formatos EN no cubiertos.
          - _EF_GASTOS_ENTRADA_RE: "gastos de entrada [del] X%" — formulación
            alternativa en ES (la actual solo cubre "comisión de suscripción"
            y "costes de entrada", no "gastos de entrada" como sinónimo).
          - _EF_CARGO_INICIAL_RE: "cargo inicial [máximo del] X%" — Robeco ES
            y algunas gestoras alemanas con KIIDs traducidos.
          Impacto estimado: reducción de los 134 nulos actuales en ~40-60 fondos
          (depende de análisis completo de los 134 KIIDs).

          Nuevos patrones de ZERO para EXIT FEE:
          - _XF_NO_EXIT_CHARGE_EN_RE: "no exit charge" / "no redemption charge"
            / "no exit fee" — fondos EN sin comisión de salida.
          - _XF_NO_COBRAREMOS_RE: "no cobraremos comisión de reembolso" /
            "no se cobra comisión de reembolso" — ES alternativo al patrón
            actual "no cobramos".

          Nuevos patrones de PORCENTAJE para EXIT FEE (completitud):
          - _XF_FRONT_EXIT_EN_RE: "exit charge [of] X%" / "redemption fee X%"
            — complementa _EXIT_FEE_RE que ya cubre "exit charge|exit fee"
            pero falla en formatos sin "Costes de salida" como trigger.
          - _XF_JPM_FUSED_ZERO: JPMorgan OCR fusionado para salida:
            "costesdesalida0,00%nocobramoscomisión" — equivalente al
            _EF_JPM_FUSED_ZERO ya existente para entrada.
          - _XF_JPM_FUSED_PCT: JPMorgan OCR fusionado: "costesdesalidaX,XX%"
            — equivalente al _EF_JPM_FUSED_PCT para entrada.

          Control SQL de validación post-ejecución:
          SELECT COUNT(*) FROM fund_master WHERE Entry_Fee_Pct IS NULL
            AND Fee_Known_Flag = 'NOT_FOUND';
          -- Objetivo: reducción desde 134 a ~75-90

          SELECT COUNT(*) FROM fund_master WHERE Exit_Fee_Pct IS NULL;
          -- Objetivo: reducción desde 735 a ~680-710

  NOTA BL-51B: La extensión de schema para comisiones con estructura mixta
          (porcentaje + tope fijo en EUR) se trata en documento separado
          BL51_SCHEMA_DECISION.md. No se implementa en esta versión hasta
          completar el análisis de impacto en P3.

Cambios v23 (2026-04-19) — BL-41 + BL-43 (media prioridad) + nuevo ítem Subtype:

  BL-41   _detect_style_profile(): detecta Growth/Value/Income desde texto KIID.
          Señales estrictas (solo cuando el KIID declara explícitamente el estilo):
          - Growth ES/EN: "estilo growth", "growth-oriented companies", "above-average
            growth prospects", "empresas de crecimiento"
          - Value ES/EN:  "estilo value", "empresas infravaloradas", "undervalued",
            "value-oriented approach"
          - Income ES/EN: "orientado a ingresos", "generación de rentas", "alta
            rentabilidad por dividendo", "dividend-paying companies", "high income"
          Aplicación: PASO 10j, solo sobre fondos RV (fund_nature='Renta Variable').
          Impacto estimado: ~72 fondos RV con Style_Profile NULL → valor asignado.

  BL-43a  _detect_subtype_monetario(): detecta LVNAV / VNAV / CNAV.
          Señal primaria: texto KIID (patrones regulatorios EU de MMF).
          Señal secundaria: nombre del fondo (LVNAV/VNAV/CNAV en el nombre es
          100% fiable — usado por JPMorgan sistemáticamente).
          Resuelve el solapamiento semántico Family/Subtype en Monetarios:
          Family mantiene su valor actual (Monetario/LVNAV/VNAV/CNAV) sin
          modificación; Subtype captura la especificidad estructural regulatoria
          de forma explícita e independiente.
          Impacto estimado: 18 fondos JPMorgan + otros potenciales con texto KIID.

  BL-43b  _detect_subtype_mixtos(): detecta Fixed Band / Volatility Target.
          - Fixed Band: banda fija de RV explícita en nombre del fondo.
            Patrón: DMAS SRI 15/50/75 y STRATEGY 15/50/75 (Allianz).
            El número de la banda se preserva en el Subtype (ej: "Fixed Band 15").
          - Volatility Target: señal desde texto KIID ("volatilidad objetivo",
            "target volatility", "risk control", "nivel de riesgo objetivo").
          Impacto estimado: 10 fondos Fixed Band + 1 Volatility Target.
          Nuevos valores Subtype permitidos:
            Monetario → LVNAV | VNAV | CNAV
            Mixtos    → Fixed Band {N} | Volatility Target

Cambios v22 (2026-04-19) — BL-37b + BL-35b + BL-40 (tres items alta prioridad backlog):

  BL-37b  _OC_FUSED_PATTERNS: nuevo patrón JPMorgan texto OCR 100% fusionado.
          Layout: "comisionesdegestiónyotros1,90%delvalordesuinversiónalaño"
          Los fondos JPMorgan con Language=None no matcheaban ningún patrón OC
          existente porque _OC_DEL_VALOR_RE requiere espacios en "comisiones de
          gestión y otros". La nueva entrada en _OC_FUSED_PATTERNS busca en
          t_fused (text.lower().replace(" ","")) y captura el porcentaje.
          Impacto: Ongoing_Charge NULL baja de 270 a ~93 (177 JPMorgan resueltos).

  BL-35b  _EF_AXA_NO_FACTURA (prioridad 7 ZERO) + _EF_THREAD_DISTRIB (prioridad 15):
          - AXA: "Nosotros no facturamos el coste de entrada" → 0.0 / ZERO_CONFIRMED
            (24 fondos AXA con €0 de entrada correctamente marcados como NOT_FOUND).
          - Thread: "Costes de entrada Se incluyen costes de distribución del X%"
            El porcentaje de distribución es la comisión de entrada real en este
            layout. Grupo 1 puede tener espacio interno ("X , XX") → se elimina
            antes de parsear.
          Impacto: Fee_Known_Flag NOT_FOUND baja de 223 a ~144 (79 resueltos).

  BL-40   _ACCUM_PATTERNS_ES: 3 nuevos patrones Deutsche/DWS + BlackRock.
          - Deutsche/DWS: "acciones del fondo son de acumulación" (103 fondos)
          - Deutsche/DWS: "rendimientos y ganancias no se reparten sino que se reinvierten"
            (alternativa textual validada en mismos KIIDs)
          - BlackRock: "acciones serán no distributivas" (95 fondos)
          Impacto: Accumulation_Policy NULL baja de 594 a ~396 (198 resueltos).

Cambios v21 (2026-04-18) — análisis de 591 fondos NOT_FOUND de 5 gestoras:

  EF-JPM-1   _EF_JPM_FUSED_ZERO / _EF_JPM_FUSED_PCT: JPMorgan produce texto
             OCR 100% fusionado sin espacios (Language=None). Los patrones
             estándar no matchean. Los nuevos buscan en t_fused (text.replace(" ","")):
             - ZERO: "costesdeentrada 0,00%" → 0.0 (54 fondos)
             - PCT:  "costesdeentrada X,XX%delimporte" → float (125 fondos)
             Cobertura: 179/181 = 99%.

  EF-SCH-1   _EF_SCH_BRACKET / _EF_SCH_NO_COBRAR: Schroeder (SISF).
             - BRACKET: "Costes de entrada ... Hasta EUR NNN ... [X.XX%]"
               El porcentaje real está entre corchetes (162 fondos).
             - NO_COBRAR: "Costes de entrada No cobramos comisión de entrada.
               EUR 0" — no matchea _ENTRY_FEE_ZERO_RE por usar "EUR 0"
               en lugar de "0 EUR" o "0%" (47 fondos → ZERO_CONFIRMED).
             Cobertura: 162/163 = 99%.

  EF-UBS-1   _EF_UBS_PCT_BEFORE / _EF_UBS_CIFRAS / _EF_UBS_NO_INICIAL: UBS.
             - PCT_BEFORE: "X.X% del importe que usted paga ... Costes de
               entrada Hasta EUR NNN". El porcentaje precede al label (102 fondos).
             - CIFRAS: "cifras incluyen la comisión de suscripción máxima ...
               hasta el X.XX%" (4 fondos).
             - NO_INICIAL: "No aplicamos una comisión inicial" → 0.0 (6 fondos).
             Cobertura: 112/112 = 100%.

  EF-MG-1    _EF_MG_PCT_BEFORE / _EF_MG_ZERO: M&G.
             - PCT_BEFORE: "X,XX% del valor de su inversión. Se trata del
               coste de entrada máximo que Costes de entrada €NNN,NN cobrará
               M&G." El porcentaje precede al label (59 fondos).
             - ZERO: "Costes de entrada €0,00" / "$0,00" → 0.0 (7 fondos).
             Cobertura: 66/66 = 100%.

  EF-AM-1    _EF_AMUNDI_DISTRIB / _EF_AMUNDI_PUEDE: Amundi.
             - DISTRIB: "costes de distribución del X,XX% del importe
               invertido ... Costes de entrada Hasta NNN EUR" (66 fondos).
             - PUEDE: "Puede cobrarse hasta el X,XX% de su inversión antes
               de que se le pague" — layout alternativo Amundi/IE (5 fondos).
             Cobertura: 66/69 = 96%.

  TOTAL BL-35: 585/591 NOT_FOUND resueltos (99%). Los 6 restantes son
             estructuralmente irrecuperables: KIIDs incompletos o estatutos
             societarios en francés (no son DDF estándar).

Cambios v20 (2026-04-18) — (incluidos en este fichero, encabezado corregido):

  BL-38-v20  _BENCH_TERMINATORS ampliado con terminadores validados en
             datos reales post-v19: además, través, último, canal(es),
             management, limited, bank, centre, business, route, avenida,
             calle, street, road. Nuevo patrón "\\),?\\s+[a-z]{3,}" para
             cortar tras cierre de paréntesis seguido de texto contaminante.

  ACC-v20    _ACCUM_PATTERNS_ES ampliado con 3 patrones validados (>=97%).
  DIST-v20   _DIST_PATTERNS_ES ampliado con 2 patrones validados (>=98%).
  SFDR-v20   _detect_sfdr_article reestructurado con prioridad máxima para
             patrones categóricos explícitos. Corrige 30 fondos Franklin
             clasificados incorrectamente como Art.9.

Cambios v19 (2026-04-16) — análisis de 3.204 KIIDs reales:

  OC-DWS-1   _OC_DEL_VALOR_RE: nuevo patrón para DWS/Deutsche/Natixis.
              Layout: "Comisiones de gestión y otros [...] X,XX% del valor
              de su inversión al año." El patrón PRIIPs anterior fallaba
              porque no hay trigger "incidencia de costes" y el número va
              seguido de "del valor de su inversión", no de "%" inline.
              Recupera ~410 fondos Deutsche/Natixis/Amundi con OC NULL.

  OC-ALLIANZ-1 _OC_CADA_ANNO_RE: nuevo patrón para Allianz.
              Layout: "Incidencia anual de los costes (*)\n\nEn caso de salida\n
              N EUR\nX,X % cada afio". El separador largo con importe EUR
              superaba el límite [^0-9]{0,60} del patrón PRIIPS.
              El nuevo patrón usa [\\s\\S]{0,500}? + "cada año/afio/aio/aho".
              Recupera ~45 fondos Allianz con OC NULL.

  EF-COBRARLE-1 _ENTRY_FEE_COBRARLE_RE: nuevo patrón para AXA/Pictet/Waystone.
              Layout: "cobrarle hasta (un máximo del) X.XX%".
              El patrón anterior buscaba el porcentaje directamente después
              del trigger, fallando cuando hay texto intermedio largo.
              Recupera ~165 fondos con entry fee NOT_FOUND.

  EF-NINGUNA-1 _ENTRY_FEE_NINGUNA_RE: detecta "Ninguna" tras trigger de entrada.
              Recupera ~203 fondos con ZERO_CONFIRMED (antes NOT_FOUND).

  XF-NINGUNA-1 _EXIT_FEE_NINGUNA_RE: detecta "Ninguna" tras trigger de salida.
              Layout dominante: "Costes de salida Ninguna" (354 fondos).
              Recupera ~535 fondos con exit fee ZERO confirmado.

  XF-COBRARLE-1 _EXIT_FEE_COBRARLE_RE: "cobrarle hasta X%" para salida.
              Recupera ~456 fondos con exit fee valor positivo.


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

Cambios v20 (2026-04-18):

  BL-38-v20   _BENCH_TERMINATORS ampliado con terminadores validados en
              datos reales post-v19: además, través, último, canal(es),
              management, limited, bank, centre, business, route, avenida,
              calle, street, road. Nuevo patrón "\\),?\\s+[a-z]{3,}" para
              cortar tras cierre de paréntesis seguido de texto
              contaminante. Resuelve los 18 benchmarks residuales.

  ACC-v20     _ACCUM_PATTERNS_ES ampliado con 3 patrones validados (>=97%
              precisión contra fondos ya clasificados):
                - "(clase|acciones|participaciones|subfondo) de acumulación"
                - "(acumula|acumulan|capitaliza) (ingresos|rentas|...)"
                - "(ingresos|rentas|...) ... se reinvierten" (forma general)
              Recupera ~199 fondos con Accumulation_Policy NULL.

  DIST-v20    _DIST_PATTERNS_ES ampliado con 2 patrones validados (>=98%
              precisión) usando separador [ \\t]+ en lugar de \\s+ para
              evitar cruzar saltos de línea del OCR:
                - "(distribuyen|paga|reparte) dividendos (anual|trimestral|...)"
                - "(se distribuirán|pagarán|repartirán) (ingresos|rentas|dividendos)"

  DIST-FIX    Corrección del patrón "política de distribución[^.]{0,80}distribuye"
              que capturaba "política de distribución ... no distribuye" como
              señal de DISTRIBUTION (en realidad ACCUMULATION). Añadido
              grupo no-capturante "(?:(?!no\\s+distribuye)[^.]){0,80}" en la
              ventana para excluir la frase negativa.
              Resuelve falsos positivos DIST del parser v19.

  SFDR-v20    _detect_sfdr_article reestructurado con prioridad máxima
              para patrones categóricos explícitos (validados 100% en
              119 fondos de datos reales):
                Prioridad 0: "Categoría según SFDR Artículo N" (Franklin)
                Prioridad 1: "Artículo N del SFDR"
                Prioridad 2-4: patrones heurísticos anteriores (Art.9/8/6)
              Corrige bug histórico: 30 fondos Franklin clasificados como
              Art.9 eran realmente Art.6 según el KIID. El parser anterior
              tomaba "artículo 9" de cualquier sección del texto (incluso
              en secciones informativas/referenciales) como señal Art.9.
              La captura con grupo "(\\d)" toma el número del patrón
              categórico y lo devuelve directamente, evitando la heurística
              de búsqueda por subcadenas.

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
    if entry_fee is not None and entry_fee > 0 and _fee_is_ceiling(kiid_text, "entry"):
        # Part 1 regla A: comision condicional/techo -> punto NULL (techo vive en *_Max)
        result["Fee_Known_Flag"] = "ENTRY_CONDITIONAL"
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            "ENTRY_FEE[CONDITIONAL->NULL]"
        )
    elif entry_fee is not None:
        result["Entry_Fee_Pct"] = entry_fee
        result["Fee_Known_Flag"] = "ZERO_CONFIRMED" if entry_fee == 0.0 else "EXTRACTED"
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"ENTRY_FEE[{entry_fee:.4f}][{result['Fee_Known_Flag']}]"
        )
    else:
        result["Fee_Known_Flag"] = "NOT_FOUND"

    # -------------------------------------------------
    # PASO 10d — Exit_Fee_Pct (comisión de salida) — v26 BL-55
    # Tres casos:
    #   A. _detect_exit_fee() devuelve float no-None → EXIT_EXPLICIT_ZERO
    #      (0.0) o valor positivo. El Flag de entry_fee no cambia.
    #   B. _detect_exit_fee() devuelve None pero _infer_exit_fee_from_structure()
    #      devuelve 0.0 → EXIT_INFERRED_ZERO (cero estructural).
    #   C. Ambas devuelven None → Exit_Fee_Pct=NULL (sin cambio de flag).
    # -------------------------------------------------
    exit_fee = _detect_exit_fee(kiid_text)
    _exit_conditional = (
        exit_fee is not None and exit_fee > 0
        and _fee_is_ceiling(kiid_text, "exit")
    )
    if _exit_conditional:
        # Part 1 regla A: comision de salida condicional/techo -> punto NULL.
        # NO inferir cero estructural (no es cero, es indeterminado).
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            "EXIT_FEE[CONDITIONAL->NULL]"
        )
    elif exit_fee is not None:
        result["Exit_Fee_Pct"] = exit_fee
        # Distinguir cero explícito de valor positivo
        _exit_flag = "EXIT_EXPLICIT_ZERO" if exit_fee == 0.0 else "EXIT_EXTRACTED"
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"],
            f"EXIT_FEE[{exit_fee:.4f}][{_exit_flag}]"
        )
    else:
        # Intentar inferencia estructural (BL-55)
        exit_fee_inferred = _infer_exit_fee_from_structure(kiid_text)
        if exit_fee_inferred is not None:
            result["Exit_Fee_Pct"] = exit_fee_inferred
            result["Fee_Known_Flag"] = "EXIT_INFERRED_ZERO"
            result["Inference_Trace"] = _append_trace(
                result["Inference_Trace"],
                "EXIT_FEE[0.0000][EXIT_INFERRED_ZERO]"
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

    # -------------------------------------------------
    # PASO 10j — Style_Profile (BL-41 v23)
    # Solo Renta Variable. Señales estrictas desde texto KIID.
    # fund_nature no está disponible aquí (viene del bloque de clasificación),
    # por lo que se pasa None → detección sin restricción de Nature.
    # El pipeline aplica el resultado solo si classification["Style_Profile"]
    # ya es None (no sobreescribe lo que el bloque especializado determinó).
    # -------------------------------------------------
    sp = _detect_style_profile(kiid_text, fund_nature=None)
    if sp:
        result["Style_Profile"] = sp
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"STYLE_PROFILE[{sp}]"
        )

    # -------------------------------------------------
    # PASO 10k — Subtype Monetario + Mixtos (BL-43 v23)
    # fund_nature desconocido aquí → se detectan señales para ambas familias.
    # El pipeline decide qué valor usar en función de Fund_Nature del bloque.
    # Orden: intentar Monetario primero (más específico), luego Mixtos.
    # -------------------------------------------------
    _sub_mon = _detect_subtype_monetario(kiid_text, fund_name)
    _sub_mix = _detect_subtype_mixtos(kiid_text, fund_name)
    # Empaquetar ambos en campos auxiliares; el pipeline resuelve cuál aplicar
    if _sub_mon:
        result["_Subtype_Monetario"] = _sub_mon
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"SUBTYPE_MON[{_sub_mon}]"
        )
    if _sub_mix:
        result["_Subtype_Mixtos"] = _sub_mix
        result["Inference_Trace"] = _append_trace(
            result["Inference_Trace"], f"SUBTYPE_MIX[{_sub_mix}]"
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
        "Style_Profile":          None,   # v23 BL-41: Growth | Value | Income (solo RV)
        "Subtype":                None,   # v23 BL-43: LVNAV/VNAV/CNAV | Fixed Band N | Volatility Target
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

# P12: Patrones LIMITED (uso acotado/limitado de derivados)
ES_DERIVATIVES_LIMITED = [
    r"\b(?:puede|podr[aá])\s+(?:utilizar|emplear|usar)\s+(?:instrumentos\s+)?derivados\s+"
    r"(?:con\s+fines\s+de\s+cobertura|de\s+manera\s+limitada|de\s+forma\s+accesoria)",
    r"\buso\s+(?:limitado|moderado|accesorio)\s+de\s+(?:instrumentos\s+)?derivados\b",
    r"\bderivados\s+(?:únicamente|solo|exclusivamente)\s+con\s+fines\s+de\s+cobertura\b",
    r"\bderivados\s+(?:con\s+fines\s+de\s+)?cobertura\b(?!.{0,40}inversi[oó]n)",
]

EN_DERIVATIVES_LIMITED = [
    r"\bmay\s+use\s+(?:financial\s+)?derivatives\s+for\s+(?:hedging|efficient\s+portfolio\s+management)\b",
    r"\blimited\s+use\s+of\s+(?:financial\s+)?derivatives\b",
    r"\bderivatives\s+(?:only|solely|exclusively)\s+for\s+hedging\b",
]

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
    """
    Detecta uso de derivados: YES, LIMITED, NO.
    P12: añadido LIMITED y default NO cuando no hay mención.
    """
    if not text:
        return None

    t = text.lower()
    t_nospace = t.replace(" ", "")

    # ── Texto OCR fusionado ──────────────────────────────────────────
    if language is None:
        if any(p in t_nospace for p in ["noderivados", "noderivado"]):
            return "NO"
        if any(p in t_nospace for p in ["derivadosuso:", "instrumentosderivados",
                                          "usodederivados", "derivadosuso"]):
            return "YES"
        return None

    # ── Español ──────────────────────────────────────────────────────
    if language in ("ES", None):
        # NO tiene prioridad
        for rx in ES_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        # LIMITED antes de YES (P12)
        for rx in ES_DERIVATIVES_LIMITED:
            if re.search(rx, t):
                return "LIMITED"
        for rx in ES_DERIVATIVES_YES:
            if re.search(rx, t):
                return "YES"
        if "instrumentosderivados" in t_nospace or "usodederivados" in t_nospace:
            return "YES"

    # ── Inglés ───────────────────────────────────────────────────────
    if language in ("EN", None):
        for rx in EN_DERIVATIVES_NO:
            if re.search(rx, t):
                return "NO"
        # LIMITED antes de YES (P12)
        for rx in EN_DERIVATIVES_LIMITED:
            if re.search(rx, t):
                return "LIMITED"
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
    # BL-38: contaminaciones detectadas en datos reales (texto de sección objetivo)
    r"riesgo[s]?|corro|obten|rentabilidad|producto[s]?|inversi[oó]n|p[aá]gina|"
    r"documento|agosto|julio|septiembre|octubre|noviembre|diciembre|"
    r"enero|febrero|marzo|abril|mayo|junio|"
    # v20: nuevas contaminaciones detectadas en validación real post-v19
    #   "sofr), además" → además
    #   "msci european último informe" → último
    #   "msci europe través de todos los canales" → través, canales
    #   "...management (ireland) limited, european bank and business centre..."
    r"adem[aá]s|trav[eé]s|[uú]ltimo|canal(?:es)?|management|limited|"
    r"bank\b|centre|business|route|avenida|calle|street|road|"
    r"[a-z]{15,})|"
    r"[\.;\n]|"
    r"\s{2,}|"
    r"\bwww\b|"                         # URLs sin paréntesis
    r"\s+\(www|"                        # URLs con paréntesis
    r",\s+un\s+(?:índice|index)|"       # ", un índice que no..." al final
    r"\s+\([^)]{0,6}\)\s+(?:el|la|se|un|una|para)\b|"  # parenthesis + article = adjacent text
    r"\s+\(el\s|\s+\(la\s|\s+\(un\s|"  # "(el ...) texto adjunto"
    r"[¿?]|"                            # BL-38: signo de interrogación (contaminación OCR)
    r"\),?\s+[a-z]{3,}"                 # v20: ")," o ")" seguido de texto (contaminación post-paréntesis)
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

    # BL-38-v21: índices de tipo overnight válidos (SOFR, €STR, SONIA, ESTR, TONA,
    # SARON, ESTER) son benchmarks legítimos de 4-5 chars que de otro modo caen por
    # el umbral len<6 / "sin sufijo". Ya pasaron el match de proveedor (prov_m), así
    # que devolver el token canónico es seguro. Recupera p.ej. "sofr), además" -> "sofr".
    _m_short = re.match(r'(?:€?str|sofr|sonia|saron|tona|ester|estr)\b', raw, re.IGNORECASE)
    if _m_short:
        return _m_short.group(0)

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

    # BL-38: Rechazar si contiene palabras funcionales españolas → contaminación de sección
    # Patrón: artículos/preposiciones/verbos que no aparecen en nombres de índices reales
    _CONTAMINATION_WORDS = re.compile(
        r"\b(?:riesgo|corro|podría|obtener|cambio|hemos|clasificado|producto|"
        r"página|documento|inversor|agosto|julio|septiembre|octubre)\b",
        re.IGNORECASE
    )
    if _CONTAMINATION_WORDS.search(raw):
        return None

    # BL-38: Rechazar si supera 80 caracteres Y contiene más de 6 tokens (indica frase completa)
    if len(raw) > 80:
        token_count = len(raw.split())
        if token_count > 7:
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
# BL-37: ampliado para cubrir variantes sin artículo "El" y casing distinto
_OC_DDF_MGMT_RE = re.compile(
    r"comisiones?\s+de\s+gesti[oó]n\s+y\s+otros\s+costes[^\.]{0,150}"
    r"(?:El\s+|el\s+)?([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Costes de operación (transacción)
# BL-37: ampliado para cubrir variantes sin artículo "El"
_OC_DDF_TRANS_RE = re.compile(
    r"costes?\s+de\s+operaci[oó]n[^\.]{0,150}"
    r"(?:El\s+|el\s+)?([\d]+[,.][\d]+)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Comisión de entrada = 0 explícito — DDF declara "sin comisión de entrada" (v17)
# BL-51A/2: añadidos triggers "comisión inicial" y "cargo inicial".
_ENTRY_FEE_ZERO_RE = re.compile(
    r"(?:costes?\s+de\s+entrada"
    r"|comisi[oó]n\s+de\s+(?:suscripci[oó]n|entrada|inicial)"
    r"|gastos?\s+de\s+(?:suscripci[oó]n|entrada)"
    r"|cargo\s+(?:de\s+entrada|inicial))"
    # BL-COST-ZERO-FIX (R-6): la ventana NO puede cruzar a la siguiente fila
    # de coste (salida/gestion/operacion/rendimiento). Causa raiz del falso
    # ZERO_CONFIRMED: el "0 EUR" de la fila de salida caia dentro de los 200
    # chars y la rama "\\b0...(eur|%)" lo tomaba como cero de ENTRADA, anulando
    # una comision real (ej. FR0010664052 "Hasta el 3,00%").
    r"(?:(?!costes?\s+de\s+salida|comisiones?\s+de\s+gesti"
    r"|costes?\s+de\s+operaci"
    r"|comisiones?\s+(?:de\s+(?:[eé]xito|rendimiento)|en\s+funci[oó]n))[\s\S]){0,200}"
    r"(?:no\s+se\s+cobr(?:an|a)\s+(?:gastos|comisi[oó]n)\s+de\s+entrada"
    r"|no\s+entry\s+(?:charge|fee)"
    r"|entry\s+(?:charge|fee)\s*:\s*(?:none|nil|0)"
    r"|comisi[oó]n\s+de\s+(?:suscripci[oó]n|entrada|inicial)\s*:\s*0"
    r"|gastos\s+de\s+(?:suscripci[oó]n|entrada)\s*:\s*0"
    r"|sin\s+comisi[oó]n\s+de\s+(?:suscripci[oó]n|entrada|inicial)"
    r"|\b0(?:[,.]00)?\s*(?:eur|usd|gbp|%)\b)",
    re.IGNORECASE
)

# Comisión de entrada (Entry_Fee_Pct)
# BL-35: formatos UCITS clásicos.
# BL-51A/2: nuevos triggers ES/EN; separador no-greedy (fix bug greedy);
#           decimal opcional para cubrir "5%" además de "5,00%".
_ENTRY_FEE_RE = re.compile(
    r"(?:costes?\s+de\s+entrada"
    r"|comisi[oó]n\s+(?:m[aá]xima\s+)?(?:de\s+suscripci[oó]n|de\s+entrada|inicial)"
    r"|gastos?\s+de\s+(?:suscripci[oó]n|entrada)"
    r"|cargo\s+(?:de\s+entrada|m[aá]ximo\s+de\s+entrada|inicial)"
    r"|derecho\s+de\s+suscripci[oó]n"
    r"|entry\s+(?:charge|fee|load)"
    r"|subscription\s+(?:charge|fee)"
    r"|purchase\s+(?:charge|fee)"
    r"|upfront\s+(?:charge|fee)"
    r"|sales\s+load)"
    r"[^\r\n]{0,300}?"
    r"(?:hasta\s+)?([\d]+(?:[,.][\d]+)?)\s*%",
    re.IGNORECASE | re.DOTALL
)

# ---------------------------------------------------------------------------
# BL-COST-CEILING (Part 1, regla A): deteccion de comision CONDICIONAL / techo.
# Cuando la comision de entrada/salida se expresa como techo ("Hasta el 3,00%",
# "1,00% maximo", "cobrarle hasta un 5%", "comision maxima de entrada del 3%",
# "up to X%"), el valor PUNTUAL es indeterminado -> Entry/Exit_Fee_Pct = NULL.
# El techo se conserva en *_Max (priips_cost_extractor), NO aqui.
# El marcador puede ir ANTES o DESPUES del numero, por eso la ventana cubre el
# % y ~35 chars posteriores. R-6: ventana acotada, sin cruzar a la fila siguiente.
# ---------------------------------------------------------------------------
_FEE_CEILING_MARKER_RE = re.compile(
    r"(?:hasta|m[aá]xim[oa]|up\s+to|a\s+maximum\s+of|as\s+much\s+as)",
    re.IGNORECASE,
)
_CEILING_BOUND = (
    r"(?:(?!costes?\s+de\s+salida|comisiones?\s+de\s+gesti"
    r"|costes?\s+de\s+operaci"
    r"|comisiones?\s+(?:de\s+(?:[eé]xito|rendimiento)|en\s+funci[oó]n))[\s\S]){0,200}"
)
_ENTRY_CEILING_PROBE = re.compile(
    r"(?:costes?\s+de\s+entrada"
    r"|comisi[oó]n\s+(?:m[aá]xima\s+)?de\s+(?:suscripci[oó]n|entrada|inicial)"
    r"|gastos?\s+de\s+(?:suscripci[oó]n|entrada)"
    r"|cargo\s+(?:de\s+entrada|m[aá]ximo\s+de\s+entrada|inicial)"
    r"|derecho\s+de\s+suscripci[oó]n"
    r"|entry\s+(?:charge|fee|load)|subscription\s+(?:charge|fee)"
    r"|purchase\s+(?:charge|fee)|upfront\s+(?:charge|fee)|sales\s+load)"
    + _CEILING_BOUND +
    r"\d+(?:[,.]\d+)?\s*%[\s\S]{0,35}",
    re.IGNORECASE,
)
_EXIT_CEILING_PROBE = re.compile(
    r"(?:costes?\s+de\s+salida"
    r"|comisi[oó]n\s+(?:m[aá]xima\s+)?de\s+reembolso"
    r"|gastos?\s+de\s+reembolso|cargo\s+de\s+salida"
    r"|derecho\s+de\s+reembolso|exit\s+(?:charge|fee)"
    r"|redemption\s+(?:charge|fee|load)|back[\s\-]?end\s+(?:load|charge)"
    r"|deferred\s+sales\s+charge)"
    + _CEILING_BOUND +
    r"\d+(?:[,.]\d+)?\s*%[\s\S]{0,35}",
    re.IGNORECASE,
)


def _fee_is_ceiling(text: str, side: str) -> bool:
    """True si la comision (side='entry'|'exit') se expresa como techo/condicional."""
    if not text:
        return False
    probe = _ENTRY_CEILING_PROBE if side == "entry" else _EXIT_CEILING_PROBE
    m = probe.search(text)
    return bool(m and _FEE_CEILING_MARKER_RE.search(m.group(0)))


# Comisión de salida (Exit_Fee_Pct) — valor no cero
# BL-36: formatos UCITS clásicos.
# BL-51A/2: nuevos triggers; separador no-greedy; decimal opcional.
_EXIT_FEE_RE = re.compile(
    r"(?:costes?\s+de\s+salida"
    r"|comisi[oó]n\s+(?:m[aá]xima\s+)?de\s+reembolso"
    r"|gastos?\s+de\s+reembolso"
    r"|cargo\s+de\s+salida"
    r"|derecho\s+de\s+reembolso"
    r"|exit\s+(?:charge|fee)"
    r"|redemption\s+(?:charge|fee|load)"
    r"|back[\s\-]?end\s+(?:load|charge)"
    r"|deferred\s+sales\s+charge)"
    r"[^\r\n]{0,300}?"
    r"([\d]+(?:[,.][\d]+)?)\s*%",
    re.IGNORECASE | re.DOTALL
)

# Comisión de salida = 0 explícito — DDF declara "0 EUR" o "no cobramos"
# BL-36: añadidos formatos UCITS clásicos
_EXIT_FEE_ZERO_RE = re.compile(
    r"(?:costes?\s+de\s+salida"          # plural Y singular (coste de salida)
    r"|comisi[oó]n\s+(?:m[aá]xima\s+)?de\s+reembolso"
    r"|gastos?\s+de\s+reembolso"
    r"|exit\s+(?:charge|fee)"
    r"|redemption\s+(?:charge|fee))"
    r"[\s\S]{0,200}"
    r"(?:no\s+cobr(?:amos|a)\s+(?:una\s+)?(?:comisi[oó]n|coste)\s+de\s+(?:salida|reembolso)"
    r"|nosotros\s+no\s+facturamos\s+el\s+coste\s+de\s+salida"  # v28 BL-DLA-2: AXA Ireland
    r"|\b0(?:[,.]00)?\s*(?:eur|usd|gbp|%)"
    r"|sin\s+comisi[oó]n\s+de\s+(?:salida|reembolso)"
    r"|no\s+se\s+aplica\s+comisi[oó]n\s+de\s+(?:salida|reembolso)"
    r"|no\s+(?:exit|redemption)\s+(?:charge|fee)"
    r"|no\s+hay\s+costes?\s+de\s+salida)",               # v28 BL-DLA-2: 'no hay costes de salida'
    re.IGNORECASE
)

# Patrón principal PRIIPs: captura uno o dos valores porcentuales
# BL-37: ampliado con variantes adicionales observadas en datos reales
_OC_PRIIPS_RE = re.compile(
    r'(?:incidencia\s+(?:anual\s+)?de\s+los\s+costes'
    r'|impacto\s+(?:anual\s+)?en\s+los\s+costes'
    r'|gastos\s+en\s+curso'
    r'|coste\s+total\s+anual'           # BL-37: variante tabular Amundi
    r'|total\s+(?:ongoing\s+)?charges?'  # BL-37: EN format
    r'|ongoing\s+charges?'               # BL-37: EN UCITS
    r'|total\s+expense\s+ratio'          # BL-37: TER explícito
    r'|\bter\b)'
    r'[^0-9]{0,60}'           # BL-37: ampliado de 50 a 60 chars (tablas con más espacio)
    r'([\d]+[,.][\d]+)\s*%'   # primer valor (siempre presente)
    r'(?:\s+([\d]+[,.][\d]+)\s*%)?',  # segundo valor (opcional)
    re.IGNORECASE
)

# Patrón UCITS antiguo: "Gastos corrientes X,XX%"
# BL-37: ampliado con "gastos totales" y formato sin espacio
_OC_UCITS_RE = re.compile(
    r'(?:gastos\s+corrientes|gastos\s+totales\s+anuales?|total\s+de\s+gastos)'
    r'\s*[:\|]?\s*([\d]+[,.][\d]+)\s*%',
    re.IGNORECASE
)

# Patrón fusionado (OCR sin espacios)
# BL-37: añadido patrón para "ongoingcharges" fusionado EN
_OC_FUSED_PATTERNS = [
    re.compile(r'incidenciadeloscoste[s]?[^0-9]{0,15}([\d]+[,.][\d]+)%([\d]+[,.][\d]+)?%?'),
    re.compile(r'gastoscorrientes([\d]+[,.][\d]+)%'),
    re.compile(r'ongoingcharges?([\d]+[,.][\d]+)%'),   # BL-37: EN fused
    re.compile(r'totalexpense(?:ratio)?([\d]+[,.][\d]+)%'),  # BL-37: TER fused
    # BL-37b: JPMorgan OCR 100% fusionado sin espacios
    # Layout: "comisionesdegestiónyotros1,90%delvalordesuinversiónalaño"
    re.compile(
        r'comisionesdegesti[oó]nyotros([\d]+[,\.][\d]+)%delvalordesuinversi[oó]n',
        re.IGNORECASE
    ),
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


# v19 ── Entry fee "Ninguna" explícita
# Layout: "Costes de entrada Ninguna" (Amundi, algunos fondos DDF)
_ENTRY_FEE_NINGUNA_RE = re.compile(
    r'(?:costes?\s+de\s+entrada|comisi[oó]n\s+de\s+suscripci[oó]n)'
    r'[^\r\n]{0,80}\bninguna?\b',
    re.IGNORECASE
)

# v19 ── Entry fee "cobrarle hasta (un máximo del) X.XX%" — AXA, Pictet, Waystone
# Layouts observados:
#   AXA:     "cobrarle hasta un máximo del 5.00%"
#   Pictet:  "cobrarle hasta un máximo del 3.00%."
#   Waystone:"cobrarle hasta el 4,00% del monto"
_ENTRY_FEE_COBRARLE_RE = re.compile(
    r'(?:costes?\s+de\s+entrada|comisi[oó]n\s+de\s+suscripci[oó]n)'
    r'[\s\S]{0,500}?'
    r'cobrarle\s+hasta\s+(?:un\s+m[aá]ximo\s+del\s+|el\s+)?([\d]+[,.][\d]+)\s*%',
    re.IGNORECASE
)

# v21 ── JPMorgan (texto OCR 100% fusionado sin espacios)
# Layout ZERO: "Costesdeentrada 0,00%,nocobramoscomisióndeentrada."
# Layout PCT:  "Costesdeentrada 5,00%delimportequepagaráusted"
# El texto JPMorgan no tiene ningún espacio entre palabras (Language=None).
# Los patrones buscan en texto con espacios eliminados (t_fused).
_EF_JPM_FUSED_ZERO = re.compile(
    r'costesdeentrada\s*0[,\.]00%',
    re.IGNORECASE
)
_EF_JPM_FUSED_PCT = re.compile(
    r'costesdeentrada\s*([\d]+[,\.][\d]+)%del(?:importe|valor)',
    re.IGNORECASE
)

# v21 ── Schroeder (SISF): porcentaje entre corchetes tras importe absoluto
# Layout: "Costes de entrada la cantidad máxima ... Hasta EUR 300 ... [3.00%]"
# El valor real está en corchetes; el importe EUR es base calculada sobre 10.000.
_EF_SCH_BRACKET = re.compile(
    r'Costes\s+de\s+entrada[^[]{0,400}\[([\d]+[,\.][\d]+)%\]',
    re.IGNORECASE | re.DOTALL
)
# Schroeder ZERO: "Costes de entrada No cobramos comisión de entrada. EUR 0"
# No matchea _ENTRY_FEE_ZERO_RE porque usa "EUR 0" (no "0 EUR" ni "0%").
_EF_SCH_NO_COBRAR = re.compile(
    r'Costes\s+de\s+entrada\s+No\s+cobramos\s+comisi[oó]n\s+de\s+entrada',
    re.IGNORECASE
)

# v21 ── UBS: porcentaje declarado ANTES del label "Costes de entrada"
# Layout A: "X.X% del importe que usted paga al realizar esta inversión.
#            Este es el importe máximo Costes de entrada Hasta EUR NNN..."
_EF_UBS_PCT_BEFORE = re.compile(
    r'([\d]+[,\.][\d]+)%\s+del\s+importe\s+que\s+usted\s+paga'
    r'[\s\S]{0,200}?Costes\s+de\s+entrada',
    re.IGNORECASE
)
# Layout B: "cifras incluyen la comisión de suscripción máxima ... hasta el X.XX%"
_EF_UBS_CIFRAS = re.compile(
    r'cifras\s+incluyen\s+la\s+comisi[oó]n\s+de\s+suscripci[oó]n\s+m[aá]xima'
    r'[\s\S]{0,200}?hasta\s+el\s*([\d]+[,\.][\d]+)\s*%',
    re.IGNORECASE
)
# Layout ZERO: "No aplicamos una comisión inicial"
_EF_UBS_NO_INICIAL = re.compile(
    r'No\s+aplicamos\s+una\s+comisi[oó]n\s+inicial',
    re.IGNORECASE
)

# v21 ── M&G: porcentaje declarado ANTES del label, seguido de importe absoluto
# Layout: "4,00% del valor de su inversión. Se trata del coste de entrada
#          máximo que Costes de entrada €400,00 cobrará M&G."
_EF_MG_PCT_BEFORE = re.compile(
    r'([\d]+[,\.][\d]+)%\s+del\s+valor\s+de\s+su\s+inversi[oó]n'
    r'[\s\S]{0,150}?Se\s+trata\s+del\s+coste\s+de\s+entrada\s+m[aá]ximo',
    re.IGNORECASE
)
# M&G ZERO: "Costes de entrada €0,00" / "$0,00"
_EF_MG_ZERO = re.compile(
    r'Costes\s+de\s+entrada\s+[€\$]0[,\.]00',
    re.IGNORECASE
)

# v21 ── Amundi: porcentaje en frase "costes de distribución del X,XX%"
# que precede al label "Costes de entrada"
# Layout: "costes de distribución del 5,00% del importe invertido.
#          Se trata de la cantidad máxima ... Costes de entrada Hasta 500 EUR"
_EF_AMUNDI_DISTRIB = re.compile(
    r'costes?\s+de\s+distribuci[oó]n\s+del\s+([\d]+[,\.][\d]+)\s*%'
    r'[\s\S]{0,400}?Costes\s+de\s+entrada',
    re.IGNORECASE
)
# Amundi variante: "Puede cobrarse hasta el X,XX% de su inversión antes"
_EF_AMUNDI_PUEDE = re.compile(
    r'[Pp]uede\s+cobrarse\s+hasta\s+el\s+([\d]+[,\.][\d]+)\s*%\s+de\s+su\s+inversi[oó]n\s+antes',
    re.IGNORECASE
)

# v22 BL-35b ── Thread: "costes de distribución del X% del importe"
# Layout: "Costes de entrada Se incluyen costes de distribución del X % del importe"
# El porcentaje puede tener un espacio entre dígitos y separador decimal.
_EF_THREAD_DISTRIB = re.compile(
    r'Costes\s+de\s+entrada\s+Se\s+incluyen\s+costes?\s+de\s+distribuci[oó]n\s+del\s+'
    r'([\d]+[,\.]?\s*[\d]*)\s*%',
    re.IGNORECASE
)

# v22 BL-35b ── AXA: "Nosotros no facturamos el coste de entrada" → ZERO_CONFIRMED
# Layout: "Costes de entrada Nosotros no facturamos el coste de entrada. €0"
_EF_AXA_NO_FACTURA = re.compile(
    r'nosotros\s+no\s+facturamos\s+el\s+coste\s+de\s+entrada',
    re.IGNORECASE
)

# v24 BL-51A ── Nuevos patrones ZERO entrada
# ─────────────────────────────────────────────────────────────────────────────

# "no hay gastos de entrada" — Fidelity, BNP Paribas
# Layout: "No hay gastos de entrada en este fondo."
# Layout: "No hay gastos de entrada."
_EF_NO_HAY_GASTOS_RE = re.compile(
    r'no\s+hay\s+gastos?\s+de\s+entrada',
    re.IGNORECASE
)

# "sin cargo de entrada" / "sin cargo inicial" — Vanguard, Robeco
# Layout: "sin cargo de entrada aplicable"
# Layout: "sin cargo inicial"
_EF_SIN_CARGO_RE = re.compile(
    r'sin\s+cargo\s+(?:de\s+entrada|inicial)',
    re.IGNORECASE
)

# Formatos EN sin comisión de entrada
# Layout: "no front-end load"
# Layout: "no sales charge"
# Layout: "no initial charge"
# Layout: "entry charge: nil" / "entry charge: none"
_EF_NO_FRONT_LOAD_RE = re.compile(
    r'(?:no\s+front[\s\-]?end\s+(?:load|charge|fee)'
    r'|no\s+sales\s+charge'
    r'|no\s+initial\s+charge'
    r'|entry\s+(?:charge|fee)\s*[:\-]\s*(?:nil|none|n/a|0(?:[,\.]00)?)'
    r'|initial\s+(?:charge|fee)\s*[:\-]\s*(?:nil|none|n/a|0(?:[,\.]00)?)'
    r'|no\s+(?:entry|subscription)\s+(?:charge|fee))',
    re.IGNORECASE
)

# v24 BL-51A ── Nuevos patrones PORCENTAJE entrada
# ─────────────────────────────────────────────────────────────────────────────

# "gastos de entrada [del/de hasta] X%" — sinónimo de "costes de entrada" no cubierto
# Layout: "Gastos de entrada del 3,00%"
# Layout: "gastos de entrada: hasta el 5%"
# Nota: separador decimal OPCIONAL — "5%" y "3,00%" son ambos válidos.
# Separador [\s\S]{0,200}? no-greedy para cruzar texto intermedio sin consumir el valor.
_EF_GASTOS_ENTRADA_RE = re.compile(
    r'gastos?\s+de\s+entrada'
    r'[\s\S]{0,200}?'
    r'(?::\s*hasta\s+el\s+'
    r'|hasta\s+(?:el\s+)?(?:un\s+m[aá]ximo\s+del?\s+)?'
    r'|del?\s+'
    r'|:\s*)'
    r'([\d]+(?:[,.][\d]+)?)\s*%',
    re.IGNORECASE)

# "cargo inicial [máximo del] X%" — Robeco ES, gestoras alemanas traducidas
# Layout: "cargo inicial máximo del 3,00%"
# Layout: "cargo inicial: 5,00%"
_EF_CARGO_INICIAL_RE = re.compile(
    r'cargo\s+inicial'
    r'[\s\S]{0,200}?'
    r'(?:m[aá]ximo\s+del?\s+|del?\s+|:\s*(?:hasta\s+(?:el\s+)?)?)'
    r'([\d]+(?:[,.][\d]+)?)\s*%',
    re.IGNORECASE)

# "front-end load [of] X%" / "sales charge [of up to] X%" / "initial charge [of up to] X%"
# Formatos EN con porcentaje
# Layout: "front-end load of 3.00%"
# Layout: "sales charge of up to 5%"
# Layout: "initial charge: 3.00%"
# Nota: separador [\s\S]{0,200}? no-greedy para que "of up to" no sea consumido
# por el separador antes de llegar a la alternativa de prefijo.
_EF_FRONT_LOAD_EN_RE = re.compile(
    r'(?:front[\s\-]?end\s+(?:load|charge|fee)'
    r'|sales\s+(?:charge|load)'
    r'|initial\s+(?:charge|fee)'
    r'|subscription\s+(?:charge|fee))'
    r'[\s\S]{0,200}?'
    r'(?:of\s+up\s+to\s+|of\s+|up\s+to\s+|:\s*(?:up\s+to\s+)?)?'
    r'([\d]+(?:[,.][\d]+)?)\s*%',
    re.IGNORECASE)


def _detect_entry_fee(text: str) -> Optional[float]:
    """
    Extrae la comisión de entrada (Entry_Fee_Pct) desde la sección
    "Composición de costes" del DDF/PRIIPs.

    Prioridad (v24 BL-51A):
    1.  Declaración explícita de sin comisión → 0.0 (ZERO_CONFIRMED)
    2.  "Ninguna" explícita después de trigger → 0.0
    3.  JPMorgan fused ZERO: "Costesdeentrada 0,00%,nocobramoscomisión" → 0.0
    4.  Schroeder ZERO: "Costes de entrada No cobramos comisión de entrada" → 0.0
    5.  UBS ZERO: "No aplicamos una comisión inicial" → 0.0
    6.  M&G ZERO: "Costes de entrada €0,00 / $0,00" → 0.0
    7.  AXA ZERO: "Nosotros no facturamos el coste de entrada" → 0.0  (v22 BL-35b)
    8.  "no hay gastos de entrada" → 0.0  (v24 BL-51A)
    9.  "sin cargo de entrada / inicial" → 0.0  (v24 BL-51A)
    10. EN ZERO: "no front-end load / no sales charge / no initial charge" → 0.0  (v24 BL-51A)
    11. "cobrarle hasta (un máximo del) X.XX%" — AXA/Pictet/Waystone → float
    12. JPMorgan fused PCT: "Costesdeentrada X,XX%delimporte" → float
    13. Schroeder bracket: "[X.XX%]" tras bloque entrada → float
    14. UBS PCT_BEFORE: "X.X% del importe que usted paga ... Costes de entrada" → float
    15. UBS CIFRAS: "cifras incluyen ... hasta el X.XX%" → float
    16. M&G PCT_BEFORE: "X,XX% del valor ... Se trata del coste de entrada máximo" → float
    17. Amundi DISTRIB: "costes de distribución del X,XX% ... Costes de entrada" → float
    18. Amundi PUEDE: "Puede cobrarse hasta el X,XX% de su inversión antes" → float
    19. Thread DISTRIB: "Se incluyen costes de distribución del X%" → float (v22 BL-35b)
    20. "gastos de entrada [del] X%" — sinónimo ES → float  (v24 BL-51A)
    21. "cargo inicial [máximo del] X%" — Robeco/DE → float  (v24 BL-51A)
    22. EN: "front-end load/sales charge/initial charge X%" → float  (v24 BL-51A)
    23. Valor porcentual estándar después de trigger → float
    24. Sin detección → None (NOT_FOUND)

    La distinción 0.0 vs None es crítica para Fee_Known_Flag en P3.
    """
    if not text:
        return None

    # Texto fusionado para patrones JPMorgan
    t_fused = text.replace(" ", "")

    # ── Prioridad 1: declaración explícita de sin comisión ───────────────────
    m_zero = _ENTRY_FEE_ZERO_RE.search(text) or _ENTRY_FEE_ZERO_RE.search(text.lower())
    if m_zero:
        return 0.0

    # ── Prioridad 2: "Ninguna" explícita (v19) ───────────────────────────────
    m_ninguna = _ENTRY_FEE_NINGUNA_RE.search(text) or _ENTRY_FEE_NINGUNA_RE.search(text.lower())
    if m_ninguna:
        return 0.0

    # ── Prioridad 3: JPMorgan fused ZERO ────────────────────────────────────
    if _EF_JPM_FUSED_ZERO.search(t_fused):
        return 0.0

    # ── Prioridad 4: Schroeder "No cobramos comisión de entrada" ────────────
    if _EF_SCH_NO_COBRAR.search(text):
        return 0.0

    # ── Prioridad 5: UBS "No aplicamos una comisión inicial" ────────────────
    if _EF_UBS_NO_INICIAL.search(text):
        return 0.0

    # ── Prioridad 6: M&G "Costes de entrada €0,00 / $0,00" ──────────────────
    if _EF_MG_ZERO.search(text):
        return 0.0

    # ── Prioridad 7: AXA "Nosotros no facturamos el coste de entrada" ────────
    # (v22 BL-35b) — 24 fondos AXA → ZERO_CONFIRMED
    if _EF_AXA_NO_FACTURA.search(text) or _EF_AXA_NO_FACTURA.search(text.lower()):
        return 0.0

    # ── Prioridad 8: "no hay gastos de entrada" (v24 BL-51A) ─────────────────
    if _EF_NO_HAY_GASTOS_RE.search(text) or _EF_NO_HAY_GASTOS_RE.search(text.lower()):
        return 0.0

    # ── Prioridad 9: "sin cargo de entrada / inicial" (v24 BL-51A) ───────────
    if _EF_SIN_CARGO_RE.search(text) or _EF_SIN_CARGO_RE.search(text.lower()):
        return 0.0

    # ── Prioridad 10: EN ZERO "no front-end load / no sales charge" (v24 BL-51A)
    if _EF_NO_FRONT_LOAD_RE.search(text) or _EF_NO_FRONT_LOAD_RE.search(text.lower()):
        return 0.0

    # ── Prioridad 11: "cobrarle hasta..." — AXA/Pictet/Waystone (v19) ────────
    m_cobr = _ENTRY_FEE_COBRARLE_RE.search(text) or _ENTRY_FEE_COBRARLE_RE.search(text.lower())
    if m_cobr:
        val = _parse_oc_pct(m_cobr.group(1))
        if val is not None and 0 < val <= 0.10:
            return val

    # ── Prioridad 12: JPMorgan fused PCT ─────────────────────────────────────
    m_jpm = _EF_JPM_FUSED_PCT.search(t_fused)
    if m_jpm:
        val = _parse_oc_pct(m_jpm.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 13: Schroeder bracket [X.XX%] ──────────────────────────────
    m_sch = _EF_SCH_BRACKET.search(text)
    if m_sch:
        val = _parse_oc_pct(m_sch.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 14: UBS "X.X% del importe que usted paga ... Costes de entrada"
    m_ubs_b = _EF_UBS_PCT_BEFORE.search(text)
    if m_ubs_b:
        val = _parse_oc_pct(m_ubs_b.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 15: UBS "cifras incluyen ... hasta el X.XX%" ──────────────
    m_ubs_c = _EF_UBS_CIFRAS.search(text)
    if m_ubs_c:
        val = _parse_oc_pct(m_ubs_c.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 16: M&G "X,XX% del valor ... Se trata del coste de entrada"
    m_mg = _EF_MG_PCT_BEFORE.search(text)
    if m_mg:
        val = _parse_oc_pct(m_mg.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 17: Amundi "costes de distribución del X,XX% ... Costes de entrada"
    m_am_d = _EF_AMUNDI_DISTRIB.search(text)
    if m_am_d:
        val = _parse_oc_pct(m_am_d.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 18: Amundi "Puede cobrarse hasta el X,XX%" ────────────────
    m_am_p = _EF_AMUNDI_PUEDE.search(text)
    if m_am_p:
        val = _parse_oc_pct(m_am_p.group(1))
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 19: Thread "Se incluyen costes de distribución del X%" ──────
    # (v22 BL-35b) — 55 fondos Thread con layout "Costes de entrada Se incluyen costes de distribución del X %"
    m_thr = _EF_THREAD_DISTRIB.search(text) or _EF_THREAD_DISTRIB.search(text.lower())
    if m_thr:
        raw_thr = m_thr.group(1).replace(" ", "")  # eliminar posible espacio en "X , XX"
        val = _parse_oc_pct(raw_thr)
        if val is not None and val <= 0.10:
            return val

    # ── Prioridad 20: "gastos de entrada [del] X%" (v24 BL-51A) ─────────────
    m_gas = _EF_GASTOS_ENTRADA_RE.search(text) or _EF_GASTOS_ENTRADA_RE.search(text.lower())
    if m_gas:
        val = _parse_oc_pct(m_gas.group(1))
        if val is not None and 0 < val <= 0.10:
            return val

    # ── Prioridad 21: "cargo inicial [máximo del] X%" (v24 BL-51A) ───────────
    m_car = _EF_CARGO_INICIAL_RE.search(text) or _EF_CARGO_INICIAL_RE.search(text.lower())
    if m_car:
        val = _parse_oc_pct(m_car.group(1))
        if val is not None and 0 < val <= 0.10:
            return val

    # ── Prioridad 22: EN "front-end load/sales charge/initial charge X%" (v24 BL-51A)
    # IMPORTANTE: aplicar SOLO si no matcheó _EF_NO_FRONT_LOAD_RE (prioridad 10).
    # Esta función ya no alcanza este punto si la prioridad 10 devolvió 0.0.
    m_fl = _EF_FRONT_LOAD_EN_RE.search(text) or _EF_FRONT_LOAD_EN_RE.search(text.lower())
    if m_fl:
        val = _parse_oc_pct(m_fl.group(1))
        if val is not None and 0 < val <= 0.10:
            return val

    # ── Prioridad 23: valor porcentual estándar después de trigger ───────────
    m = _ENTRY_FEE_RE.search(text) or _ENTRY_FEE_RE.search(text.lower())
    if m:
        val = _parse_oc_pct(m.group(1))
        if val is not None and val <= 0.10:
            return val

    return None


# v19 ── Exit fee "Ninguna" explícita (354 fondos en análisis)
# Layout: "Costes de salida Ninguna" / "Costes de salida No cobramos ... Ninguna"
_EXIT_FEE_NINGUNA_RE = re.compile(
    r'(?:costes?\s+de\s+salida|comisi[oó]n\s+de\s+reembolso)'
    r'[^\r\n]{0,150}\bninguna?\b',
    re.IGNORECASE
)

# v19 ── Exit fee "cobrarle hasta X%" con separador largo (Pictet, algunos fondos)
_EXIT_FEE_COBRARLE_RE = re.compile(
    r'(?:costes?\s+de\s+salida|comisi[oó]n\s+de\s+reembolso)'
    r'[\s\S]{0,400}?'
    r'cobrarle\s+hasta\s+(?:un\s+m[aá]ximo\s+del\s+|el\s+)?([\d]+[,.][\d]+)\s*%',
    re.IGNORECASE
)

# v24 BL-51A ── Nuevos patrones ZERO salida
# ─────────────────────────────────────────────────────────────────────────────

# EN: "no exit charge" / "no redemption charge" / "no exit fee"
# Layout: "no exit charge applies"
# Layout: "exit charge: nil" / "redemption fee: none"
_XF_NO_EXIT_CHARGE_EN_RE = re.compile(
    r'(?:no\s+exit\s+(?:charge|fee)'
    r'|no\s+redemption\s+(?:charge|fee)'
    r'|exit\s+(?:charge|fee)\s*[:\-]\s*(?:nil|none|n/a|0(?:[,\.]00)?)'
    r'|redemption\s+(?:charge|fee)\s*[:\-]\s*(?:nil|none|n/a|0(?:[,\.]00)?))',
    re.IGNORECASE
)

# ES alternativo: "no cobraremos comisión de reembolso" / "no se cobra comisión de reembolso"
# El patrón existente _EXIT_FEE_ZERO_RE cubre "no cobramos" pero no "no cobraremos" ni
# "no se cobra". Estos formatos aparecen en KIIDs Fidelity ES y BNP.
_XF_NO_COBRAREMOS_RE = re.compile(
    r'(?:costes?\s+de\s+salida|comisi[oó]n\s+de\s+reembolso)'
    r'[\s\S]{0,300}'
    r'(?:no\s+cobraremos\s+(?:comisi[oó]n|gastos?)\s+de\s+(?:salida|reembolso)'
    r'|no\s+se\s+cobra\s+(?:comisi[oó]n|gastos?)\s+de\s+(?:salida|reembolso)'
    r'|no\s+se\s+aplica\s+(?:comisi[oó]n|gastos?)\s+de\s+(?:salida|reembolso)'
    r'|no\s+hay\s+gastos?\s+de\s+salida)',
    re.IGNORECASE
)

# v24 BL-51A ── JPMorgan OCR fusionado para salida
# Equivalente directo a _EF_JPM_FUSED_ZERO / _EF_JPM_FUSED_PCT para entry fee.
# Layout ZERO: "Costesdesalida 0,00%nocobramoscomisióndesalida."
# Layout PCT:  "Costesdesalida 1,00%delimportedeventa"
_XF_JPM_FUSED_ZERO = re.compile(
    r'costesdesalida\s*0[,\.]00%',
    re.IGNORECASE
)
_XF_JPM_FUSED_PCT = re.compile(
    r'costesdesalida\s*([\d]+[,\.][\d]+)%del(?:importe|valor)',
    re.IGNORECASE
)

# v26 BL-55 ── Nuevos patrones ZERO explícitos para salida
# ─────────────────────────────────────────────────────────────────────────────

# ES declaración negativa directa:
# "sin comisión de salida", "sin gastos de reembolso", "sin cargos de cancelación"
# "no hay gastos de salida", "inexistentes gastos de salida"
_XF_SIN_COMISION_ES_RE = re.compile(
    r'(?:sin\s+(?:comisi[oó]n|gastos?|cargos?)\s+(?:de\s+)?(?:salida|reembolso|cancelaci[oó]n)'
    r'|(?:no\s+(?:hay|existen?)|inexistentes?)\s+(?:comisi[oó]n|gastos?|cargos?)\s+'
    r'(?:de\s+)?(?:salida|reembolso|cancelaci[oó]n))',
    re.IGNORECASE
)

# ES comisión de salida seguida de valor cero explícito:
# "comisión de salida: 0", "comisión de salida cero", "comisión de reembolso: ninguna"
# "comisión de salida: nil", "comisión de salida: n/a", "comisión de reembolso —"
_XF_ZERO_VALOR_ES_RE = re.compile(
    r'comisi[oó]n\s+de\s+(?:salida|reembolso|cancelaci[oó]n)'
    r'[\s:.\-]*'
    r'(?:0\b|0[,\.]00\s*%?|cero|ninguna?|nil|n\.?\s*a\.?|n/a|—|–|-\s*$)',
    re.IGNORECASE | re.MULTILINE
)

# EN declaración negativa directa:
# "no exit charge", "nil redemption fee", "none back-end load"
# "exit charge: none", "redemption fee: nil", "exit load: 0.00%"
_XF_NO_EN_DIRECT_RE = re.compile(
    r'(?:(?:no|nil|none|n\.?\s*a\.?)\s+(?:exit|redemption|back[\s\-]?end)\s+'
    r'(?:charge|fee|load)'
    r'|(?:exit|redemption)\s+(?:charge|fee|load)\s*[:\-]\s*'
    r'(?:0(?:\.00)?\s*%?|none|nil|n\.?a\.?|—|-\s*$))',
    re.IGNORECASE | re.MULTILINE
)

# Tabular fusionado adicional: "costesdesalida:ninguno" / "exitcharges:0.00%"
_XF_TABULAR_FUSED_RE = re.compile(
    r'(?:costesdesalida\s*[:=]\s*ninguna?s?'
    r'|exitcharges?\s*[:=]\s*0(?:\.00)?\s*%?)',
    re.IGNORECASE
)

# BL-DLA-2 (v27) -- Patron tabular PRIIPs con salto de linea
# Causa raiz de 510 fondos exit_fee_null (96% LU, formato PRIIPs estandar):
# _EXIT_FEE_RE usa separador no-newline que no cruza saltos de linea.
# En el layout PRIIPs tabulado, trigger y valor estan en lineas distintas.
# Este patron usa separador acotado 80 chars con:
#   1. Lookahead negativo: no arrancar si sigue Ninguna/no cobr/no exit.
#   2. No cruzar otra keyword de coste (evita capturar fila adyacente).
#   3. Prefijos opcionales: Hasta, Up to, importe EUR intermedio.
#   4. Limite: 0 < val <= 5%.
_XF_TABLA_PRIIPS_RE = re.compile(
    r'(?:costes?\s+de\s+salida'
    r'|comisi[oó]n\s+(?:m[aá]xima\s+)?de\s+reembolso'
    r'|gastos?\s+de\s+reembolso'
    r'|cargo\s+de\s+salida'
    r'|derecho\s+de\s+reembolso'
    r'|exit\s+(?:charge|fee)'
    r'|redemption\s+(?:charge|fee|load)'
    r'|back[\s\-]?end\s+(?:load|charge)'
    r'|deferred\s+sales\s+charge)'
    r'(?!\s*(?:ninguna?|no\s+cobr|no\s+exit|no\s+redemption|sin\s+comisi))'
    r'((?:(?!costes?\s+de\s+(?:entrada|corrientes?)|comisi[oó]n\s+de\s+gesti|ongoing\s+charge)[\s\S]){0,80}?)'
    r'(?:hasta\s+|up\s+to\s+)?'
    r'(?:eur\s+[\d,.]+\s+)?'
    r'([\d]+(?:[,.[\d]+)?)\s*%',
    re.IGNORECASE
)

# ─────────────────────────────────────────────────────────────────────────────
# v26 BL-55 ── Helper de inferencia estructural
# v26.1 BL-55/2 (2026-04-25) — Endurecimiento ventana acotada:
#   La versión inicial v26 verificaba ausencia de exit keywords en TODO el texto;
#   cualquier mención incidental (índice, glosario, ejemplo) bloqueaba la
#   inferencia. Resultado real ciclo 25/04: solo 3 fondos inferidos sobre 676
#   candidatos. v26.1 restringe la verificación a la VENTANA de la sección de
#   costes (±1500 chars), y elimina la regla '...' que era demasiado agresiva
#   (KIIDs reales contienen puntos suspensivos legítimos en muchas partes).
# ─────────────────────────────────────────────────────────────────────────────

# Keywords que confirman que la sección de costes está presente.
# Ampliada en v26.1 con variantes adicionales detectadas en muestreo manual.
_COST_SECTION_KEYWORDS = [
    "composición de costes",
    "composicion de costes",
    "composition of charges",
    "costes y gastos",
    "charges and expenses",
    "gastos y comisiones",
    "tabla de costes",
    "desglose de costes",
    "cuadro de costes",
    "estructura de costes",
    "costes del fondo",
    # Variantes EN adicionales
    "fund charges",
    "ongoing charges",
    "one-off charges",
    "this table shows the charges",
    "esta tabla muestra los gastos",
]

# Keywords de comisión de salida cuya presencia en la VENTANA de costes
# hace que NO se infiera cero estructural.
_EXIT_KEYWORDS_PRESENCE = [
    "salida", "reembolso", "cancelaci",
    "exit", "redemption", "back-end", "backend",
    "back end",
]

# Tamaño de ventana alrededor del match de cost section para verificar ausencia
# de exit keywords. Justificación: la sección de costes en KIIDs PRIIPs ocupa
# típicamente 800–1200 chars; ±1500 cubre con margen.
_COST_WINDOW_HALF = 1500


def _infer_exit_fee_from_structure(text: str) -> Optional[float]:
    """
    BL-55: Infiere Exit_Fee_Pct=0.0 cuando el KIID contiene la sección de
    costes claramente identificada y la VENTANA de esa sección NO menciona
    ninguna palabra-clave de comisión de salida.

    Principio: ausencia en sección estructurada ≡ no aplica.

    v26.1 (2026-04-25): la verificación de ausencia se acota a una ventana
    de ±1500 chars alrededor del primer match de cost section (en vez de al
    texto completo). Esto evita falsos negativos por menciones incidentales
    de "salida"/"exit" en otras partes del KIID (índice, glosario, FAQ).

    Restricciones de seguridad (no inferir si):
      - Texto < 500 chars (OCR degradado o documento truncado).
      - Texto contiene marcadores de truncado ('[truncado]', '[truncated]').
      - No se localiza ninguna sección de costes identificable.
      - text es None o vacío.

    Returns:
        0.0 si puede inferirse cero estructural.
        None en cualquier otro caso.
    """
    if not text or len(text) < 500:
        return None

    t_lower = text.lower()

    # Restricción de seguridad: marcadores explícitos de truncado
    if "[truncado]" in t_lower or "[truncated]" in t_lower:
        return None

    # Condición 1: localizar la sección de costes (primer match gana).
    cost_section_pos = -1
    for kw in _COST_SECTION_KEYWORDS:
        pos = t_lower.find(kw)
        if pos != -1:
            if cost_section_pos == -1 or pos < cost_section_pos:
                cost_section_pos = pos
    if cost_section_pos == -1:
        return None

    # Condición 2: en la VENTANA acotada (±_COST_WINDOW_HALF chars alrededor
    # del primer match), no debe aparecer ninguna keyword de comisión de salida.
    window_start = max(0, cost_section_pos - _COST_WINDOW_HALF)
    window_end = min(len(t_lower), cost_section_pos + _COST_WINDOW_HALF)
    window = t_lower[window_start:window_end]

    has_exit_mention = any(kw in window for kw in _EXIT_KEYWORDS_PRESENCE)
    if has_exit_mention:
        return None

    # Ambas condiciones satisfechas: sección estructurada de costes sin mención
    # de comisión de salida en su ventana.
    return 0.0


def _detect_exit_fee(text: str) -> Optional[float]:
    """
    Extrae la comisión de salida (Exit_Fee_Pct) desde la sección
    "Composición de costes" del DDF/PRIIPs.

    Prioridad (v26 BL-55):
    1.  Declaración explícita de cero ("no cobramos", "0 EUR") → 0.0
    2.  "Ninguna" explícita después del trigger → 0.0  (v19)
    3.  JPMorgan fused ZERO: "Costesdesalida 0,00%" → 0.0  (v24 BL-51A)
    4.  EN ZERO: "no exit charge / no redemption charge" → 0.0  (v24 BL-51A)
    5.  ES alt ZERO: "no cobraremos / no se cobra" → 0.0  (v24 BL-51A)
    6.  Porcentaje no cero — patrón estándar → float  (v19)
    7.  "cobrarle hasta X%" con separador largo → float  (v19)
    8.  JPMorgan fused PCT: "Costesdesalida X,XX%delimporte" → float  (v24)
    9.  ES negativa directa: "sin comisión de salida" / "no hay gastos" → 0.0  (v26 BL-55)
    10. ES valor cero explícito: "comisión de salida: cero/nil/0/—" → 0.0  (v26 BL-55)
    11. EN negativa directa: "no/nil/none exit charge" / "exit charge: nil" → 0.0  (v26 BL-55)
    12. Tabular fusionado adicional: "costesdesalida:ninguno" / "exitcharges:0.00%" → 0.0 (v26)
    13. Tabular PRIIPs salto de linea: "Costes de salida\n2,00%" → float  (v27 BL-DLA-2)
    14. Sin detección → None

    Nota: La distinción 0.0 vs None es crítica para P3.
    La inferencia estructural (_infer_exit_fee_from_structure) se aplica
    en el paso 10d del parser principal, DESPUÉS de esta función.
    """
    if not text:
        return None

    # Texto fusionado para patrones JPMorgan
    t_fused = text.replace(" ", "")

    # Prioridad 1: declaración explícita de cero
    m_zero = _EXIT_FEE_ZERO_RE.search(text) or _EXIT_FEE_ZERO_RE.search(text.lower())
    if m_zero:
        return 0.0

    # Prioridad 2: "Ninguna" explícita (v19)
    m_ning = _EXIT_FEE_NINGUNA_RE.search(text) or _EXIT_FEE_NINGUNA_RE.search(text.lower())
    if m_ning:
        return 0.0

    # Prioridad 3: JPMorgan fused ZERO (v24 BL-51A)
    if _XF_JPM_FUSED_ZERO.search(t_fused):
        return 0.0

    # Prioridad 4: EN ZERO "no exit charge / no redemption charge" (v24 BL-51A)
    if _XF_NO_EXIT_CHARGE_EN_RE.search(text) or _XF_NO_EXIT_CHARGE_EN_RE.search(text.lower()):
        return 0.0

    # Prioridad 5: ES alt ZERO "no cobraremos / no se cobra" (v24 BL-51A)
    if _XF_NO_COBRAREMOS_RE.search(text) or _XF_NO_COBRAREMOS_RE.search(text.lower()):
        return 0.0

    # Prioridad 6: porcentaje no cero — primero patrón estándar
    m = _EXIT_FEE_RE.search(text) or _EXIT_FEE_RE.search(text.lower())
    if m:
        val = _parse_oc_pct(m.group(1))
        if val is not None and 0 < val <= 0.05:
            return val

    # Prioridad 7: porcentaje con separador largo — "cobrarle hasta X%" (v19)
    m_cobr = _EXIT_FEE_COBRARLE_RE.search(text) or _EXIT_FEE_COBRARLE_RE.search(text.lower())
    if m_cobr:
        val = _parse_oc_pct(m_cobr.group(1))
        if val is not None and 0 < val <= 0.05:
            return val

    # Prioridad 8: JPMorgan fused PCT (v24 BL-51A)
    m_jpm_xf = _XF_JPM_FUSED_PCT.search(t_fused)
    if m_jpm_xf:
        val = _parse_oc_pct(m_jpm_xf.group(1))
        if val is not None and 0 < val <= 0.05:
            return val

    # Prioridad 9: ES negativa directa (v26 BL-55)
    if (_XF_SIN_COMISION_ES_RE.search(text)
            or _XF_SIN_COMISION_ES_RE.search(text.lower())):
        return 0.0

    # Prioridad 10: ES valor cero explícito (v26 BL-55)
    if (_XF_ZERO_VALOR_ES_RE.search(text)
            or _XF_ZERO_VALOR_ES_RE.search(text.lower())):
        return 0.0

    # Prioridad 11: EN negativa directa (v26 BL-55)
    if (_XF_NO_EN_DIRECT_RE.search(text)
            or _XF_NO_EN_DIRECT_RE.search(text.lower())):
        return 0.0

    # Prioridad 12: tabular fusionado adicional (v26 BL-55)
    if _XF_TABULAR_FUSED_RE.search(t_fused):
        return 0.0

    # Prioridad 13: tabular PRIIPs con salto de linea (v27 BL-DLA-2)
    # Cubre el caso mayoritario: trigger y valor en lineas distintas.
    # Debe ir despues de todos los patrones de cero para no capturar
    # un porcentaje positivo cuando la fila declara Ninguna o 0%.
    m_tabla = _XF_TABLA_PRIIPS_RE.search(text) or _XF_TABLA_PRIIPS_RE.search(text.lower())
    if m_tabla:
        val = _parse_oc_pct(m_tabla.group(2))
        if val is not None and 0 < val <= 0.05:
            return val

    return None


# v19 ── Patrón DWS/Deutsche/Natixis: "X,XX% del valor de su inversión al año"
# Layout: "Costes corrientes detraídos cada año\nComisiones de X,XX% del valor
#          de su inversión al año. Se trata de una estimación basada en..."
# El patrón PRIIPs falla porque no hay trigger "incidencia de costes" y el
# número está seguido de "del valor de su inversión" en vez de estar inline.
_OC_DEL_VALOR_RE = re.compile(
    r'(?:comisiones?\s+de\s+gesti[oó]n\s+y\s+otros[\s\S]{0,100}?|'
    r'costes?\s+corrientes\s+detra[ií]dos[\s\S]{0,200}?)'
    r'([\d]+[,.][\d]+)\s*%\s*del\s+valor\s+de\s+su\s+inversi[oó]n',
    re.IGNORECASE
)

# v19 ── Patrón Allianz: "X,X % cada año/afio/aio/aho" (separador largo)
# Layout: "Incidencia anual de los costes (*)\n\nEn caso de salida\ndespués de N años\n
#          M.MMM EUR\nX,X % cada afio"
# El patrón PRIIPs falla porque hay un importe en EUR entre trigger y porcentaje,
# superando el separador [^0-9]{0,60} del patrón existente.
_OC_CADA_ANNO_RE = re.compile(
    r'(?:incidencia\s+(?:anual\s+)?de\s+los\s+costes|'
    r'costes?\s+corrientes\s+detra[ií]dos)'
    r'[\s\S]{0,500}?'
    r'([\d]+[,.][\d]+)\s*%\s*cada\s+a(?:[ñn]|fi|h)o',
    re.IGNORECASE
)

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
    5. DWS/Deutsche/Natixis: "X,XX% del valor de su inversión al año" (v19)
    6. Allianz: "X,X % cada año/afio" con separador largo con importe EUR (v19)

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

    # ── 5: DWS/Deutsche/Natixis — "X,XX% del valor de su inversión al año" ────
    # Formato: "Costes corrientes detraídos cada año\nComisiones de X,XX% del valor..."
    # El patrón actual falla porque hay texto entre trigger y valor.
    # v19: buscar el porcentaje seguido de "del valor de su inversión".
    m_dws = _OC_DEL_VALOR_RE.search(text)
    if not m_dws:
        m_dws = _OC_DEL_VALOR_RE.search(text.lower())
    if m_dws:
        val = _parse_oc_pct(m_dws.group(1))
        if val is not None:
            return val

    # ── 6: Allianz — "X,X % cada año/afio/aio" (separador largo con importe EUR) ─
    # Formato: "Incidencia anual de los costes (*)\n\nEn caso de salida\nN EUR\nX,X % cada afio"
    # El patrón actual [^0-9]{0,60} no cruza el importe EUR intermedio.
    # v19: buscar "X% cada año" en ventana amplia tras trigger.
    m_ann = _OC_CADA_ANNO_RE.search(text)
    if not m_ann:
        m_ann = _OC_CADA_ANNO_RE.search(text.lower())
    if m_ann:
        val = _parse_oc_pct(m_ann.group(1))
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
    # v20 — patrones validados en 786 NULL (precisión >=97%):
    # "(clase|acciones|participaciones|subfondo) de acumulación"
    r"(?:clase|clases|acciones|participaciones?|subfondo)\s+de\s+acumulaci[oó]n",
    # "acumulan/capitaliza los ingresos/rentas"
    r"(?:acumula|acumulan|capitaliza(?:n)?)\s+(?:los\s+)?(?:ingresos|rentas|rendimientos)",
    # "los ingresos/rentas/... se reinvierten" (forma general)
    r"(?:los\s+)?(?:ingresos|dividendos|rentas|rendimientos|beneficios)"
    r"\s+(?:de\s+(?:las?\s+)?inversi(?:o?nes?))?[^\.]{0,30}?se\s+reinvierten",
    # v22 BL-40 — Deutsche/DWS (103 fondos recuperables):
    # "Las acciones del fondo son de acumulación, es decir, los rendimientos y
    #  ganancias no se reparten sino que se reinvierten"
    r"acciones?\s+del\s+fondo\s+son\s+de\s+acumulaci[oó]n",
    r"rendimientos\s+y\s+ganancias\s+no\s+se\s+reparten\s+sino\s+que\s+se\s+reinvierten",
    # v22 BL-40 — BlackRock (95 fondos recuperables):
    # "las acciones serán no distributivas (los ingresos por dividendo se incorporarán a su valor)"
    r"acciones?\s+ser[aá]n\s+no\s+distributivas?",
]

_DIST_PATTERNS_ES = [
    r"clase\s+de\s+distribuci[oó]n",
    r"clase\s+de\s+acciones?\s+de\s+distribuci[oó]n",
    r"distribuye\s+(?:dividendos|rentas|ingresos)",
    r"reparte\s+(?:dividendos|rentas)",
    r"participaciones?\s+de\s+distribuci[oó]n",
    r"pol[íi]tica\s+de\s+distribuci[oó]n(?:(?!no\s+distribuye)[^\.]){0,80}distribuye",
    # v20 — patrones validados en 786 NULL (precisión >=98%):
    # "distribuyen/paga/reparte dividendos periódicamente"
    # Separadores sin \n para evitar cruzar frases disjuntas del OCR
    r"(?:distribuy|reparte|paga)[aeiou]*[nr]?[ \t]+(?:los[ \t]+)?dividendos[ \t]+"
    r"(?:anual|trimestral|mensual|semestral|peri[oó]dic)",
    # "se distribuirán/pagarán/repartirán ingresos/rentas/dividendos"
    r"(?:se[ \t]+)?(?:distribuir[aá]n?|pagar[aá]n?|repartir[aá]n?)[ \t]+"
    r"(?:los[ \t]+)?(?:ingresos|rentas|dividendos)",
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

    Prioridad (v20):
      0. Patrón categórico explícito "Categoría según SFDR Artículo N" (100%)
      1. Patrón formal "Artículo N del SFDR" (100%)
      2. Art. 9 — objetivo de inversión sostenible
      3. Art. 8 — promueve características medioambientales/sociales
      4. Art. 6 — declara explícitamente que no es Art.8/9

    Devuelve 9, 8 o 6. NULL si no se puede determinar.
    """
    if not text:
        return None
    t = text.lower()

    # ── Prioridad 0: "Categoría según SFDR Artículo N" (patrón Franklin) ──
    # Validado 100% precisión en 119 fondos de los datos reales.
    # Corrige el bug por el cual fondos Art.6 se clasifican como Art.9 si
    # el KIID menciona "artículo 9" en otra sección informativa.
    m_cat = re.search(
        r'categor[ií]a\s+seg[uú]n\s+(?:el\s+)?sfdr\s+(?:art[ií]culo|article)\s+(\d)',
        t, re.IGNORECASE)
    if m_cat:
        art = int(m_cat.group(1))
        if art in (6, 8, 9):
            return art

    # ── Prioridad 1: "Artículo N del SFDR" / "Article N of SFDR" ────────
    # Validado 100% precisión para Art.8 y Art.9.
    m_art_sfdr = re.search(
        r'(?:art[ií]culo|article)\s+(\d)\s+del?\s+sfdr',
        t, re.IGNORECASE)
    if m_art_sfdr:
        art = int(m_art_sfdr.group(1))
        if art in (6, 8, 9):
            return art

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
# STYLE_PROFILE — Growth / Value / Income  (BL-41 v23)
# Solo se aplica a fondos de Renta Variable.
# Señales estrictas: el KIID debe declarar explícitamente el estilo.
# =================================================

# Growth ── ES
_STYLE_GROWTH_ES = re.compile(
    r'estilo\s+(?:de\s+inversi[oó]n\s+)?(?:de\s+tipo\s+)?growth'
    r'|empresas?\s+(?:con\s+)?(?:alto\s+)?potencial\s+de\s+crecimiento'
    r'|orientad[ao]s?\s+al?\s+crecimiento\s+(?:de\s+(?:los\s+)?beneficios|del\s+valor|del\s+capital)',
    re.IGNORECASE
)
# Growth ── EN
_STYLE_GROWTH_EN = re.compile(
    r'growth\s+(?:investment\s+)?(?:style|approach|strategy)'
    r'|growth[\-\s]oriented\s+(?:companies|securities|stocks|equities)'
    r'|companies?\s+(?:with\s+)?(?:above[\-\s]average\s+)?growth\s+(?:prospects?|potential|characteristics)'
    r'|focus(?:es|ing)?\s+on\s+(?:high[\-\s])?growth\s+companies?',
    re.IGNORECASE
)
# Value ── ES
_STYLE_VALUE_ES = re.compile(
    r'estilo\s+(?:de\s+inversi[oó]n\s+)?(?:de\s+tipo\s+)?value'
    r'|empresas?\s+infravaloradas?'
    r'|inversi[oó]n\s+(?:en\s+)?(?:estilo\s+)?valor'
    r'|cotizaci[oó]n\s+(?:por\s+)?(?:debajo|inferior)\s+(?:de\s+)?su\s+valor',
    re.IGNORECASE
)
# Value ── EN
_STYLE_VALUE_EN = re.compile(
    r'value\s+(?:investment\s+)?(?:style|approach|strategy)'
    r'|undervalued\s+(?:companies?|securities|stocks|equities)'
    r'|value[\-\s]oriented\s+(?:approach|strategy|companies?)'
    r'|trading\s+(?:at\s+a\s+)?(?:discount|below)\s+(?:to\s+)?(?:their\s+)?(?:intrinsic\s+|fair\s+)?value',
    re.IGNORECASE
)
# Income ── ES
_STYLE_INCOME_ES = re.compile(
    r'orientad[ao]s?\s+(?:a\s+(?:la\s+)?generaci[oó]n\s+de\s+)?(?:rentas?|ingresos?)'
    r'|generaci[oó]n\s+(?:regular\s+)?(?:de\s+)?rentas?'
    r'|alta\s+rentabilidad\s+por\s+dividendo'
    r'|acciones?\s+(?:que\s+)?pagan?\s+dividendos?',
    re.IGNORECASE
)
# Income ── EN
_STYLE_INCOME_EN = re.compile(
    r'income[\-\s](?:oriented|focused|generating)\s+(?:approach|strategy|companies?)'
    r'|high\s+(?:dividend[\-\s])?(?:yield|income)'
    r'|dividend[\-\s]paying\s+(?:companies?|stocks?|equities)'
    r'|focus(?:es|ing)?\s+on\s+(?:generating\s+)?(?:regular\s+)?income',
    re.IGNORECASE
)


def _detect_style_profile(text: str, fund_nature: Optional[str] = None) -> Optional[str]:
    """
    Detecta el estilo de inversión desde texto KIID (BL-41 v23).

    Solo aplica a Renta Variable. Señales estrictas: el KIID debe declarar
    explícitamente el estilo de inversión — no se infiere de contexto genérico.

    Prioridad: Growth > Value > Income
    Devuelve 'Growth', 'Value', 'Income' o None.
    """
    if not text:
        return None
    # Aplicar solo a RV si se pasa fund_nature; si no se pasa, detección liberal
    if fund_nature is not None and fund_nature != "Renta Variable":
        return None

    if _STYLE_GROWTH_ES.search(text) or _STYLE_GROWTH_EN.search(text):
        return "Growth"
    if _STYLE_VALUE_ES.search(text) or _STYLE_VALUE_EN.search(text):
        return "Value"
    if _STYLE_INCOME_ES.search(text) or _STYLE_INCOME_EN.search(text):
        return "Income"
    return None


# =================================================
# SUBTYPE — Monetario: LVNAV / VNAV / CNAV  (BL-43a v23)
# Resuelve solapamiento semántico Family/Subtype en monetarios:
# Family mantiene su valor; Subtype captura la especificidad regulatoria.
# =================================================

# Texto KIID — regulatorio EU MMF (Reglamento 2017/1131)
_MON_LVNAV_TEXT = re.compile(
    r'\bLVNAV\b'
    r'|Low\s+Volatility\s+(?:Net\s+Asset\s+Value|NAV)'
    r'|valor\s+liquidativo\s+de\s+baja\s+volatilidad',
    re.IGNORECASE
)
_MON_CNAV_TEXT = re.compile(
    r'\bCNAV\b'
    r'|Constant\s+(?:Net\s+Asset\s+Value|NAV)'
    r'|valor\s+liquidativo\s+constante',
    re.IGNORECASE
)
_MON_VNAV_TEXT = re.compile(
    r'\bVNAV\b'
    r'|Variable\s+(?:Net\s+Asset\s+Value|NAV)'
    r'|valor\s+liquidativo\s+variable',
    re.IGNORECASE
)
# Nombre del fondo — JPMorgan incluye la sigla sistemáticamente
_MON_LVNAV_NAME = re.compile(r'\bLVNAV\b', re.IGNORECASE)
_MON_CNAV_NAME  = re.compile(r'\bCNAV\b',  re.IGNORECASE)
_MON_VNAV_NAME  = re.compile(r'\bVNAV\b',  re.IGNORECASE)


def _detect_subtype_monetario(
    text: str,
    fund_name: Optional[str] = None,
) -> Optional[str]:
    """
    Detecta el subtipo estructural regulatorio de un fondo monetario (BL-43a v23).

    Prioridad: texto KIID > nombre del fondo.
    Orden LVNAV > CNAV > VNAV (de menor a mayor variabilidad de NAV, CNAV
    antes de VNAV porque es más restrictivo y específico).

    Devuelve 'LVNAV', 'CNAV', 'VNAV' o None.
    """
    if text:
        if _MON_LVNAV_TEXT.search(text):
            return "LVNAV"
        if _MON_CNAV_TEXT.search(text):
            return "CNAV"
        if _MON_VNAV_TEXT.search(text):
            return "VNAV"

    if fund_name:
        n = fund_name.upper()
        if _MON_LVNAV_NAME.search(n):
            return "LVNAV"
        if _MON_CNAV_NAME.search(n):
            return "CNAV"
        if _MON_VNAV_NAME.search(n):
            return "VNAV"

    return None


# =================================================
# SUBTYPE — Mixtos: Fixed Band / Volatility Target  (BL-43b v23)
# =================================================

# Fixed Band — señal desde nombre del fondo.
# Patrón Allianz: "DMAS SRI 15", "DMAS SRI 50", "STRATEGY 15", "STRATEGY 75".
# El número (15, 20, 25, 30, 50, 75) es la banda máxima de RV en pct.
_MIX_BANDA_NAME = re.compile(
    r'(?:DMAS\s+(?:SRI\s+)?|STRATEGY\s+|STRATEG(?:IE|Y)\s+)(\d{1,3})\b',
    re.IGNORECASE
)

# Volatility Target — señal desde texto KIID.
_MIX_VOL_TARGET = re.compile(
    r'volatilidad\s+(?:anual\s+)?objetivo'
    r'|objetivo\s+de\s+(?:volatilidad|riesgo)'
    r'|nivel\s+de\s+(?:volatilidad|riesgo)\s+objetivo'
    r'|volatility\s+target'
    r'|target\s+(?:volatility|risk(?:\s+level)?)'
    r'|risk\s+control\s+(?:fund|strategy|approach)'
    r'|managed\s+volatility',
    re.IGNORECASE
)


def _detect_subtype_mixtos(
    text: str,
    fund_name: Optional[str] = None,
) -> Optional[str]:
    """
    Detecta el subtipo estructural de un fondo mixto (BL-43b v23).

    Prioridad: Volatility Target (KIID) > Fixed Band (nombre).
    - Fixed Band N: banda fija de RV máxima. El número de la banda se preserva
      en el valor (ej: 'Fixed Band 15') para utilidad directa en P3.
    - Volatility Target: gestión orientada a volatilidad/riesgo objetivo.

    Devuelve 'Fixed Band N', 'Volatility Target' o None.
    """
    if text and _MIX_VOL_TARGET.search(text):
        return "Volatility Target"

    if fund_name:
        m = _MIX_BANDA_NAME.search(fund_name)
        if m:
            pct = int(m.group(1))
            # Validar que el número es una banda de RV coherente (5-95%)
            if 5 <= pct <= 95:
                return f"Fixed Band {pct}"

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
