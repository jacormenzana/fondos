# shared/db.py
# -*- coding: utf-8 -*-
"""
Conexion a la base de datos operacional (PostgreSQL 17) para P1/P2/P3.

Uso desde cualquier modulo:
    from shared.db import get_connection

SQLite fue retirado (2026-09-26, FND-0102): esta capa es solo Postgres. Los parametros
`db_path` y `backend` de get_connection() se conservan unicamente para no tocar de golpe cada
entry point; `db_path` se ignora y `backend` solo admite None/"postgres".

Notas de diseno que siguen vigentes:

**Sin traductor automatico de placeholders.** Cada funcion escribe su propio SQL con `%s`. `?` no
se traduce porque es tambien el operador jsonb de Postgres (`?`, `?|`, `?&`); una sustitucion ciega
corromperia consultas sobre `gold.fund_scores.score_detail` o
`bronze.fund_kiid_metadata.processing_breakdown`.

**Acceso a filas.** Toda conexion usa `_NamedRow` como row_factory: soporta `row[0]` Y `row["col"]`,
y al iterar devuelve valores en orden de columna (no claves). Con `dict_row`, `list(row)` devolveria
los NOMBRES de columna y acabarian escritos como datos en los informes (descubierto portando
export_metrics.build_estado()); el codigo del repositorio usa ambos estilos de acceso.

**`with conn:` de psycopg3 hace COMMIT y luego CIERRA la conexion** (verificado 2026-09-20). En una
conexion de larga vida usa `db_transaction(conn)`.
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent   # c:/desarrollo/fondos
sys.path.insert(0, str(_ROOT))

# Side effect on purpose: importing shared.config autoloads the repo's .env (FONDOS_PG_DSN, ...) into the
# environment. Every process that connects imports this module, so this is what makes the DSN available.
# (It used to come for free through `from shared.config import DB_PATH`; removing DB_PATH silently dropped
# the autoload from every entry point — caught by the first live run after the SQLite retirement.)
import shared.config  # noqa: E402,F401

try:
    import psycopg
    from psycopg.types.numeric import FloatLoader as _psycopg_float_loader
except ImportError:  # pragma: no cover
    psycopg = None
    _psycopg_float_loader = None


class _NamedRow:
    """Fila psycopg3 subscriptable por int O por str; iterar devuelve valores (no claves), en orden
    de columna. Ver la nota "Acceso a filas" del docstring del modulo."""
    __slots__ = ("_columns", "_index", "_values")

    def __init__(self, columns: list, index: dict, values: tuple):
        self._columns = columns
        self._index = index
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._columns)

    def __repr__(self):
        return f"<Row {dict(zip(self._columns, self._values))}>"

    def __eq__(self, other):
        # Igualdad por contenido entre filas distintas (el codigo de escritura compara filas para
        # deduplicar / diff); sin esto siempre daria False.
        if isinstance(other, _NamedRow):
            return self._columns == other._columns and self._values == other._values
        return NotImplemented

    def __hash__(self):
        return hash((tuple(self._columns), tuple(self._values)))


def _named_row_factory(cursor):
    """Protocolo row-factory de psycopg3: (cursor) -> (values) -> fila. El indice de columnas se
    construye una vez por consulta y se comparte entre todas las filas del resultado."""
    # cursor.description es None en sentencias sin columnas de resultado (SET, DDL...).
    columns = [d.name for d in cursor.description] if cursor.description else []
    index = {name: i for i, name in enumerate(columns)}

    def make_row(values):
        return _NamedRow(columns, index, values)

    return make_row


# Se lee de forma perezosa: importar shared.db nunca exige que FONDOS_PG_DSN este definido.
_PG_DSN_ENV_VAR = "FONDOS_PG_DSN"
_DB_BACKEND_ENV_VAR = "FONDOS_DB_BACKEND"


def execute_fail_soft(conn, sql: str, params=()) -> bool:
    """Ejecuta una sentencia que nunca debe propagar un fallo ni envenenar la transaccion
    circundante (el patron `try: conn.execute(...); except: pass` de P1: ingestion_log, etc.).
    Postgres aborta la transaccion ENTERA ante cualquier sentencia fallida, asi que se envuelve en
    un SAVEPOINT: un fallo revierte solo esta sentencia. Devuelve True si tuvo exito.

    En modo autocommit no hay transaccion circundante (SAVEPOINT lanzaria NoActiveSqlTransaction) y
    cada sentencia ya es su propia transaccion, asi que basta el try/except."""
    if conn.autocommit:
        try:
            conn.execute(sql, params)
            return True
        except Exception:
            return False
    conn.execute("SAVEPOINT fail_soft_sp")
    try:
        conn.execute(sql, params)
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT fail_soft_sp")
        return False
    else:
        conn.execute("RELEASE SAVEPOINT fail_soft_sp")
        return True


@contextmanager
def fail_soft_block(conn):
    """Version en bloque de execute_fail_soft(): protege la transaccion con un SAVEPOINT alrededor de
    varias sentencias y RE-LANZA el fallo (el llamador conserva su propio try/except y su log):

        try:
            with fail_soft_block(conn):
                conn.execute(...)
        except Exception as e:
            print(f"WARN: {e}")

    No llames a conn.commit() DENTRO del bloque: destruye el SAVEPOINT antes del RELEASE. En
    autocommit es un paso directo."""
    if not conn.autocommit:
        conn.execute("SAVEPOINT fail_soft_block_sp")
        try:
            yield
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT fail_soft_block_sp")
            raise
        else:
            conn.execute("RELEASE SAVEPOINT fail_soft_block_sp")
    else:
        yield


def executemany(conn, sql: str, params_seq) -> None:
    """psycopg3 no tiene Connection.executemany (solo Cursor): centralizado aqui (P#11)."""
    conn.cursor().executemany(sql, params_seq)


def round_sql(expr: str, decimals: int) -> str:
    """Fragmento SQL ROUND() para double precision.

    Postgres solo tiene ROUND(numeric, integer): el CAST a numeric "limpia" dobles ruidosos
    (28.749999999999996 -> 28.75) y cambia el desempate, y round(double) de un solo argumento
    redondea mitades al par. La formula de abajo (escalar, redondear mitad-alejandose-de-cero con
    floor+sign, desescalar) se mantiene en doble precision -- sin Decimal en el lado Python -- y
    reproduce los valores historicos del informe (validada contra q_consistencia, 3683 filas,
    100% igual; q_tendencia difiere en el ultimo decimal en 0.058% de celdas, aceptado)."""
    scale = 10 ** decimals
    return (
        f"(sign(({expr})::double precision) * "
        f"floor(abs(({expr})::double precision) * {scale} + 0.5) / {scale})"
    )


def int_cast_sql(expr: str) -> str:
    """CAST a entero TRUNCANDO hacia cero (el CAST de Postgres sobre double redondea al par; el
    historico del informe trunca: 4.9999999 -> 4)."""
    return f"trunc(({expr})::double precision)::integer"


def db_transaction(conn):
    """Equivalente de `with conn:` (commit al salir bien / rollback ante excepcion) que NO cierra la
    conexion. Usa `with db_transaction(conn):`. Un `with conn:` desnudo de psycopg3 hace COMMIT y
    luego CIERRA la conexion; en una conexion de larga vida romperia toda llamada posterior."""
    return conn.transaction()


def in_transaction(conn) -> bool:
    """True si conn tiene ya una transaccion abierta a la que el llamador debe AÑADIR sus escrituras
    en vez de abrir la suya (patron EFF-2 de proyecto2). psycopg3 (autocommit=False): el servidor
    solo esta IDLE justo tras conectar o tras el ultimo commit/rollback."""
    import psycopg.pq
    return conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE


def begin_immediate(conn) -> None:
    """Abre una transaccion (`BEGIN`). Llamar solo si `not in_transaction(conn)`. Postgres usa MVCC y
    bloqueos de fila que ESPERAN en vez de lanzar, asi que no hay bucle de reintento."""
    conn.execute("BEGIN")


def table_columns(conn, table: str) -> set:
    """Conjunto de nombres de columna de `table` (information_schema), restringido al search_path de
    la conexion (`current_schemas(false)`) para resolver la misma tabla sin cualificar que
    resolveria el SQL del llamador. Postgres pliega a minusculas los identificadores sin comillas:
    quien compare contra un catalogo en mixed-case (p. ej. shared.config.DOMAIN_VALUES) debe
    plegar mayusculas el mismo."""
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = ANY(current_schemas(false))",
        (table,),
    ).fetchall()
    return {r[0] for r in rows}


def _pg_dsn() -> str:
    import os
    dsn = os.environ.get(_PG_DSN_ENV_VAR)
    if not dsn:
        raise RuntimeError(
            f"get_connection() requires {_PG_DSN_ENV_VAR} to be set "
            "(e.g. postgresql://fondos_app@127.0.0.1:5436/fondos). Password via PGPASSWORD env "
            "var or a .pgpass file — never on the command line or hardcoded."
        )
    return dsn


_BACKEND_ANNOUNCED = False


def _announce_backend(conn, backend: str, source: str) -> None:
    """Una vez por proceso, di a que base de datos se conecto (FND-0068). Nunca imprime el DSN,
    solo host/port/dbname."""
    global _BACKEND_ANNOUNCED
    if _BACKEND_ANNOUNCED:
        return
    _BACKEND_ANNOUNCED = True
    i = conn.info
    print(f"[DB] backend={backend} ({source}) host={i.host} port={i.port} dbname={i.dbname}",
          file=sys.stderr, flush=True)


def get_connection(
    db_path: Optional[Path] = None, *, backend: Optional[str] = None
) -> "psycopg.Connection":
    """Conexion psycopg3 a la base de datos apuntada por FONDOS_PG_DSN.

    `db_path` se ignora (legado de SQLite). `backend` solo admite None o "postgres" (se lee tambien
    FONDOS_DB_BACKEND, que solo puede valer "postgres"); cualquier otro valor lanza ValueError.

    Lanza RuntimeError si FONDOS_PG_DSN no esta definida o psycopg3 no esta instalado.
    """
    if backend is not None:
        _source = "arg"
    else:
        import os
        _source = "env" if _DB_BACKEND_ENV_VAR in os.environ else "default"
        backend = os.environ.get(_DB_BACKEND_ENV_VAR, "postgres")
    if backend != "postgres":
        raise ValueError(
            f"backend {backend!r} no existe: SQLite fue retirado (2026-09-26, FND-0102); "
            "solo 'postgres'."
        )
    if psycopg is None:
        raise RuntimeError(
            "psycopg3 is required (pip install 'psycopg[binary]') — not installed in this environment."
        )
    conn = psycopg.connect(_pg_dsn(), row_factory=_named_row_factory)
    # AVG()/SUM() sobre enteros (y cualquier ::numeric) llegan como decimal.Decimal, que rompe la
    # aritmetica con floats, los dtypes de pandas y las escrituras a Excel (un Decimal se escribe
    # como texto). El DDL no tiene columnas NUMERIC (todo real almacenado es double precision), asi
    # que esto solo afecta a expresiones calculadas: se registra una vez para todo llamador (P#11).
    conn.adapters.register_loader("numeric", _psycopg_float_loader)
    # db/pg/00_roles_schemas.sql fija el search_path por ALTER ROLE; se fija tambien aqui por si el rol
    # de la conexion no es fondos_app/fondos_owner. Se hace COMMIT enseguida: un SET sin LOCAL se
    # deshace con el ROLLBACK de la transaccion que lo emitio (visto en el export_metrics por hoja).
    conn.execute("SET search_path = gold, silver, bronze, control, public")
    conn.commit()
    _announce_backend(conn, "postgres", _source)
    return conn
