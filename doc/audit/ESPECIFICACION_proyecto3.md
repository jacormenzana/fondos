# Arquitectura del Proyecto

El sistema de gestión y selección de fondos se estructura en un modelo
cuantitativo de **cuatro capas independientes**. Esta arquitectura
garantiza la separación de responsabilidades: la evaluación del universo
(L1), la predicción de habilidad (L2), la asignación de capital (L3) y
la ejecución económica (L4).

A continuación, se detalla el ámbito, lógica, entidades, interfaces y
parametrización de cada una de las capas.

------------------------------------------------------------------------

# Capa 1: Elegibilidad (Eligibility)

## Ámbito y Objeto

Actúa como el filtro de entrada al sistema. Su objetivo es determinar
qué fondos del universo total son aptos para ser evaluados, garantizando
la calidad de los datos, la disponibilidad de historial suficiente y el
cumplimiento de restricciones estructurales y normativas.

## Lógica y Criterios Implementados

-   **Filtros duros (Hard Filters):** Exclusión de fondos con datos
    corruptos, documentos faltantes (`WRONG_DOC`) o en estado de
    cuarentena.
-   **Suficiencia estadística:** Exigencia de un historial mínimo de
    precios (NAV) continuo para garantizar la validez de los cálculos
    estadísticos sin sesgo de ventana corta.
-   **Frescura de datos:** El último NAV reportado no debe exceder un
    límite máximo de antigüedad.
-   **Supervivencia y ciclo de vida:** Monitorización de fondos
    fusionados o liquidados mediante la serie de presencia en catálogos.

## Funcionalidad

Depurar el universo bruto de fondos y entregar un subconjunto limpio y
viable a la capa de scoring.

## Entidades y Módulos

-   `fund_master`: Atributos estáticos y estado del documento
    (KID/KIID).
-   `fund_scorer.check_hard_filters`: Motor de aplicación de reglas
    excluyentes.
-   `shared.transfer_eligibility`: Evaluación de la estructura del
    vehículo (UCITS vs. ETF).

## Interfaces

### Entrada

-   Datos crudos (proveedores, catálogos, scraping de NAVs).

### Salida (Hacia L2)

-   Universo de fondos elegibles en la fecha de evaluación (*t*).

## Parametrizaciones Clave

-   `MIN_NAV_ROWS`: Historial mínimo de datos continuos requerido.
-   `P3_FUND_NAV_MAX_AGE_DAYS`: Límite máximo de días de antigüedad
    permitidos para el NAV.
-   Tipos de vehículos permitidos (Open-End UCITS, Money Market, etc.).

------------------------------------------------------------------------

# Capa 2: Predicción, Scoring y Auditoría de Regímenes (Doble Motor)

## Ámbito y Objeto

Es el núcleo analítico del sistema, bifurcado para resolver el conflicto
entre el rigor estadístico a largo plazo y la agilidad táctica.

Su objetivo se divide en dos misiones complementarias:

1.  **Auditar la habilidad real (El Juez):** Evaluar de forma empírica y
    libre de sesgos el alfa histórico real (*Net Real Appraisal Ratio*),
    separando la habilidad de la suerte mediante control de falsos
    descubrimientos (FDR) y series temporales robustas.
2.  **Navegar los ciclos de mercado (El Navegador):** Detectar cambios
    de régimen y tendencias en tiempo real con modelos dinámicos para
    proteger la cartera y adaptar los pesos tácticos antes de que los
    indicadores rezagados reaccionen.

------------------------------------------------------------------------

## Arquitectura de Subcapas

### Subcapa 2.1: Motor de Scoring y Auditoría (El "Juez" del Track Record)

#### Objetivo

Auditoría profunda de fondos y estimación de alfa estructural a largo
plazo.

#### Lógica y Criterios Implementados

-   **Target Predictivo:** Ratio de Evaluación Real Neto sobre ventanas
    de largo plazo.
-   **Ventanas Solapadas y Corrección Newey-West:** Uso masivo de datos
    mensuales mediante *rolling windows* con corrección econométrica de
    errores estándar (Newey-West) para evitar la autocorrelación y
    aprovechar toda la serie temporal sin inflar la significancia
    estadística.
-   **Control de Suerte (FDR) y Encogimiento (Shrinkage):** Estimación
    empírico-bayesiana de la habilidad del gestor frente a la media de
    su categoría y grupo homólogo (*peers*).
-   **Costes Estructurales:** Impacto determinista del
    `ongoing_charge_recurrent` sobre el alfa neto esperado.

#### Entidades y Módulos

-   `fund_metrics` (métricas Point-in-Time auditadas).
-   `series_benchmark` (factores macro e históricos).
-   Módulo de análisis de estilo (`fund_scorer` y regresiones de
    factores).

#### Salida

-   Ranking histórico de fondos calificados por alfa real, con bandas de
    incertidumbre estadística.

------------------------------------------------------------------------

### Subcapa 2.2: Motor de Predicción y Alocación (El "Navegador" Táctico)

#### Objetivo

Detección de cambios de fase económica y ajuste de riesgo dinámico,
eliminando el retraso (*lag*) de las medias móviles a largo plazo.

#### Lógica y Criterios Implementados

-   **Modelos Probabilísticos de Regímenes (HMM):** Algoritmos de Markov
    Ocultos que leen volatilidad y retornos recientes para calcular en
    tiempo real la probabilidad de estar en regímenes simplificados y
    robustos (Expansión / Contracción, Risk-On / Risk-Off).
-   **Inyección de Indicadores Adelantados (Leading Indicators):**
    Ingesta de variables de estrés de liquidez (diferenciales de
    crédito, pendientes de curvas de tipos) que anticipan los giros del
    mercado.
-   **Dinámica de Corto Plazo (EMAs):** Sustitución de señales estáticas
    de 36 meses por Medias Móviles Exponenciales (EMA) o ponderadas para
    capturar la tendencia subyacente sin caer en el ruido diario.

#### Entidades y Módulos

-   `macro_indicators_stream` (curvas, diferenciales, liquidez).
-   `regime_inference_engine` (motor HMM de inferencia del estado
    actual).
-   `tactical_allocator_weights` (generador de señales de
    sobreponderación/infraponderación de riesgo).

#### Salida

-   Matriz de probabilidad de régimen actual y factores de sesgo táctico
    para ajustar los pesos de la cartera hacia la Capa 3.

------------------------------------------------------------------------

## Interfaces del Sistema Integrado

### Entrada

-   Universo elegible procedente de la Capa 1.
-   Flujos de datos macroeconómicos de alta frecuencia.

### Salida (Hacia la Capa 3)

1.  Ranking estructural de fondos con su alfa calibrado (*Juez*).
2.  Señal de régimen de mercado actual y factores de riesgo táctico
    (*Navegador*).

## Parametrizaciones Clave

### Para el Juez

-   Ventanas móviles de 36 meses con solapamiento mensual.
-   Corrección de errores mediante Newey-West.
-   Agrupación por exposición real a factores (`Fund_Nature`).

### Para el Navegador

-   Ventanas de observación de volatilidad rápida (21 a 63 días).
-   Parámetros de transición de estados HMM.
-   Umbrales de activación para diferenciales de crédito.

------------------------------------------------------------------------

# Capa 3: Construcción de Cartera (Construction / Allocation)

## Ámbito y Objeto

Traducir las puntuaciones individuales de los fondos en una propuesta de
asignación de activos. Define qué fondos integran la cartera objetivo y
con qué pesos, respetando el mandato y la diversificación.

## Lógica y Criterios Implementados

-   **Deduplicación de Familias:** Limitación estricta a una única clase
    de acción por familia de fondos (`fund_family_id`) para evitar falsa
    diversificación, seleccionando siempre la clase más barata
    accesible.
-   **Asignación Neutral (Wrapper):** Ponderación equiponderada (*Equal
    Weight*) dentro de los bloques de asignación macro, garantizando que
    los resultados medidos provengan del motor de selección (L2) y no de
    la concentración de la cartera.
-   **Límites de Transición:** Restricción de desviaciones mediante
    topes definidos (`TRANSITION_MAX_DEVIATION`) para evitar cambios
    bruscos de perfil.

## Funcionalidad

Ensamblar el portfolio teórico u objetivo ideal con base en las
predicciones actuales, antes de evaluar la conveniencia económica de
ejecutar los cambios.

## Entidades y Módulos

-   `portfolio_builder`
-   `portfolio_engine`

## Interfaces

### Entrada

-   Lista de fondos puntuados.
-   Predicciones de rentabilidad/riesgo procedentes de la Capa 2.

### Salida (Hacia L4)

-   Cartera objetivo propuesta (fondos y pesos ideales).

## Parametrizaciones Clave

-   Pesos por subcartera (Defensiva, Equilibrada y Dinámica).
-   `TRANSITION_MAX_DEVIATION` (ejemplo: 10% por subcartera).
-   Límites de exposición por divisa.
-   Límites de concentración de activos.

------------------------------------------------------------------------

# Capa 4: Ejecución y Rotación (Rotation / Execution)

## Ámbito y Objeto

Decidir, desde un punto de vista puramente económico y de fricción,
cuándo y cómo transicionar de la cartera actual a la cartera objetivo
propuesta por la Capa 3.

Actúa como el mecanismo de histéresis del sistema.

## Lógica y Criterios Implementados

-   **Función de Coste Económico (Expected Benefit \> Pessimistic
    Cost):** La rotación únicamente se aprueba si el incremento de
    retorno esperado (`Delta Score`) supera el coste pesimista de
    ejecución, el impacto fiscal y un margen de seguridad.
-   **Tratamiento Fiscal por Tramo:** Modelización de impuestos sobre
    plusvalías según la transferibilidad del vehículo (UCITS vs. ETF).
-   **Fricción Bid-Ask y Comisiones:** Inclusión de horquillas de
    mercado, costes de intermediación y penalizaciones por tiempo fuera
    del mercado.
-   **Rotación por Brecha Estructural (ROT-0):** Mecanismo de salida
    automática ante pérdida de elegibilidad, alarmas críticas o
    expulsión del catálogo.

## Funcionalidad

Evaluar la conveniencia económica de la rotación y generar las órdenes
definitivas de rebalanceo (*trades*).

## Entidades y Módulos

-   `should_rotate`
-   `rotation_plan`
-   Control de Histéresis.
-   Ledger de Ejecuciones (A'8).

## Interfaces

### Entrada

-   Cartera objetivo propuesta por la Capa 3.
-   Cartera real activa.
-   Parámetros de fricción.

### Salida Final

-   Plan definitivo de rotación.
-   Órdenes de compra y venta.
-   Cartera ejecutada.

## Parametrizaciones Clave

-   Cuadrícula de estrés de costes (*k-grid*).
-   Banda de histéresis (ejemplo: 0,10).
-   `DILUTION_ALLOWANCE` (10 a 25 bps).
-   `redemption_days` (días estimados fuera de mercado).
-   Tipo impositivo marginal sobre ganancias patrimoniales.
