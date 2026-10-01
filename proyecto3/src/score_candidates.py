# proyecto3/src/score_candidates.py
# -*- coding: utf-8 -*-
"""
Candidatos elegibles de fund_scores para construir una cartera (FND-0171).

Fuente unica de "que filas de fund_scores son candidatos HOY", compartida por
PortfolioBuilder y Backtester (P#11: antes cada uno llevaba su propia copia de
la consulta, y las dos tenian el mismo defecto).

Defecto que corrige: ambas consultas tomaban, por (fondo, bloque, version),
la fila MAS RECIENTE por as_of_date sin ningun limite de antiguedad. Un fondo
que deja de puntuarse -- porque la deduplicacion por familia elige otra clase,
porque pierde sus metricas (FND-0164, FND-0168) o porque sale del universo --
conservaba para siempre su ultima fila elegible como candidato vivo. En vivo
(2026-10-01): 2 de los 30 fondos de la cartera construida salieron de filas
del 2026-03-21, y 659 de 3.093 candidatos no eran de la ultima ejecucion.

Regla: un candidato debe provenir de la ULTIMA EJECUCION de scoring del
(bloque, score_version, regimen), es decir as_of_date = MAX(as_of_date) de ese
trio. Una ejecucion de score_funds() puntua todo el universo, asi que una fila
mas antigua no es "otra ejecucion valida", es un fondo que hoy no se puntua.
Como as_of_date forma parte de la PK, esto deja a lo sumo UNA fila por fondo y
bloque, lo que ademas hace imposible por construccion el bug anterior (una fila
elegible antigua "resucitando" bajo una mas reciente inelegible): la
elegibilidad se evalua sobre la fila de la ultima ejecucion y solo sobre ella.

Supuesto documentado: la ultima ejecucion es la del universo completo. Una
ejecucion de scoring parcial (p.ej. una muestra) persistida con
as_of_date posterior dejaria fuera al resto; score_funds() no tiene modo
parcial, y los datos de prueba deben respetarlo.
"""

import pandas as pd

CANDIDATE_COLUMNS = [
    "block", "isin", "score_total",
    "fund_name", "fund_nature", "management_company", "fund_family_id",
]


def load_current_candidates(conn: "psycopg.Connection",
                            score_version: str,
                            regime: str,
                            subportfolio: str | None = None) -> pd.DataFrame:
    """Candidatos elegibles de la ultima ejecucion de scoring, ordenados por
    (block, score_total DESC). `subportfolio=None` devuelve todos los bloques.

    Columnas: CANDIDATE_COLUMNS. DataFrame vacio (con esas columnas) si no hay
    ninguno. Filtra ademas a fondos con In_Current_Universe = 1.
    """
    rows = conn.execute("""
        SELECT fs.block, fs.isin, fs.score_total,
               fm.Fund_Name, fm.Fund_Nature, fm.Management_Company,
               fm.fund_family_id
        FROM fund_scores fs
        JOIN fund_master fm ON fm.ISIN = fs.isin
        WHERE fs.score_version = %s
          AND fs.regime = %s
          AND (%s::text IS NULL OR fs.block = %s::text)
          AND fs.as_of_date = (
              SELECT MAX(cur.as_of_date) FROM fund_scores cur
              WHERE cur.block = fs.block
                AND cur.score_version = fs.score_version
                AND cur.regime = fs.regime)
          AND fs.eligible = 1
          AND fs.score_total > 0
          AND fm.In_Current_Universe = 1
        ORDER BY fs.block, fs.score_total DESC
    """, (score_version, regime, subportfolio, subportfolio)).fetchall()
    return pd.DataFrame([tuple(r) for r in rows], columns=CANDIDATE_COLUMNS)
