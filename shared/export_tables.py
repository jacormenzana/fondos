# shared/export_tables.py
# -*- coding: utf-8 -*-
"""
Motor genérico de exportación de tablas SQLite a Excel.

Cualquier proyecto puede reutilizar esta función para volcar sus tablas
a un Excel con formato consistente. Cada proyecto define su propia
configuración (qué tablas, qué columnas excluir o incluir, nombres de
hoja, etc.) y llama a export_tables() con esa configuración.

Uso típico desde un módulo de proyecto:

    from shared.export_tables import export_tables, TableExportConfig

    TABLES = [
        TableExportConfig(
            table="fund_master",
            sheet_name="1_FundMaster",
            exclude_cols=["Inference_Trace", "Raw_KIID_Text"],
        ),
        TableExportConfig(
            table="fund_kiid_metadata",
            sheet_name="2_KIID",
            include_cols=["ISIN", "KIID_Status", "SRRI", "SRRI_Validation_Status"],
        ),
    ]

    out = export_tables(
        tables=TABLES,
        output_path=Path("out/mi_export_20260401.xlsx"),
        db_path=DB_PATH,
    )

Cambios v17:
  - include_cols: lista blanca de columnas a exportar (complemento a
    exclude_cols). Cuando se especifica, solo se exportan esas columnas
    en ese orden. Resuelve el bug de export_p1.py (~3.5MB vs ~50MB):
    Raw_KIID_Text estaba siendo excluida implícitamente por lógica
    en export_p1.py; ahora puede incluirse explícitamente con include_cols.
  - SELECT explícito por columna cuando include_cols está definido,
    en lugar de SELECT * (evita columnas binarias o pesadas no deseadas).
  - Errores por tabla se acumulan y se reportan al final, sin abortar
    el export completo por un fallo en una tabla.
  - chunk_size: parámetro opcional para tablas muy grandes (streaming
    por bloques con pd.read_sql_query + chunksize).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from shared.db import get_connection, table_columns

# Postgres migration (Stage 9, 2026-09-23 — the export/reporting port the plan's original Stage 7
# named but that was silently dropped when Stage 7 was renumbered): Postgres returns timestamptz
# as tz-aware datetimes, which DataFrame.to_excel refuses outright ("Excel does not support
# datetimes with timezones"). Rendered as naive wall-clock time in the project's operating zone —
# the same zone pg_seed.py assumed when it promoted SQLite's naive timestamp text to timestamptz.
_EXPORT_TZ = "Europe/Madrid"


# ============================================================
# Configuración de tabla
# ============================================================

@dataclass
class TableExportConfig:
    """
    Configuración de exportación para una tabla.

    Atributos:
        table         Nombre de la tabla en SQLite.
        sheet_name    Nombre de la hoja en el Excel resultante.
                      Si es None se usa el nombre de la tabla.
        exclude_cols  Columnas a omitir del SELECT *.
                      Ignorado si include_cols está definido.
        include_cols  Lista blanca de columnas a exportar (en ese orden).
                      Si está definido, se hace SELECT explícito y se
                      ignora exclude_cols.
        row_limit     Límite de filas exportadas. None = sin límite.
        order_by      Cláusula ORDER BY opcional (sin la palabra ORDER BY).
        where         Cláusula WHERE opcional (sin la palabra WHERE).
        chunk_size    Si > 0, lee la tabla en bloques de ese tamaño
                      (útil para tablas con millones de filas).
    """
    table:        str
    sheet_name:   Optional[str]   = None
    exclude_cols: list[str]       = field(default_factory=list)
    include_cols: Optional[list[str]] = None
    row_limit:    Optional[int]   = None
    order_by:     Optional[str]   = None
    where:        Optional[str]   = None
    chunk_size:   int             = 0

    @property
    def effective_sheet(self) -> str:
        return self.sheet_name or self.table

    def build_query(self, conn) -> str:
        """
        Construye el SELECT SQL para esta configuración.

        Si include_cols está definido → SELECT explícito de esas columnas.
        Si solo exclude_cols → SELECT * y se filtra el DataFrame después.

        Las columnas de include_cols se resuelven sin distinguir mayúsculas y se citan con la
        grafía REAL de la BD: Postgres pliega a minúsculas al crear (`isin`, no `ISIN`) y un
        identificador citado distingue mayúsculas, así que `"ISIN"` fallaría allí aunque el
        llamante (que sigue usando la grafía de SQLite) pida exactamente la columna que existe.
        """
        if self.include_cols:
            # Verificar que las columnas existen en la tabla
            existing = {c.lower(): c for c in table_columns(conn, self.table)}
            valid_cols = [existing[c.lower()] for c in self.include_cols if c.lower() in existing]
            missing    = [c for c in self.include_cols if c.lower() not in existing]
            if missing:
                # Advertir pero no abortar — se exportan las que existen
                print(f"    AVISO [{self.table}]: columnas no encontradas "
                      f"en include_cols: {missing}")
            col_str = ", ".join(f'"{c}"' for c in valid_cols)
            query = f"SELECT {col_str} FROM {self.table}"
        else:
            query = f"SELECT * FROM {self.table}"

        if self.where:
            query += f" WHERE {self.where}"
        if self.order_by:
            query += f" ORDER BY {self.order_by}"
        if self.row_limit:
            query += f" LIMIT {self.row_limit}"

        return query


# ============================================================
# Helpers internos
# ============================================================

def _resolve_writable_path(path: Path) -> Path:
    """Return path if writable; if locked (open in Excel), return a _HHMMSS variant."""
    if not path.exists():
        return path
    try:
        path.open("r+b").close()
        return path
    except PermissionError:
        ts = datetime.now().strftime("%H%M%S")
        alt = path.with_stem(f"{path.stem}_{ts}")
        print(f"  [AVISO] {path.name} bloqueado (¿abierto en Excel?). Escribiendo en {alt.name}")
        return alt


def _read_table_df(conn, query: str, chunk_size: int = 0) -> pd.DataFrame:
    """
    Ejecuta `query` y devuelve un DataFrame, idéntico en ambos motores.

    Sustituye a pd.read_sql_query, que sobre una conexión psycopg3 (no SQLAlchemy) emite un
    aviso y depende de cómo pandas trate las filas del row_factory compatible con sqlite3.Row;
    aquí el cursor se lee explícitamente (mismo criterio que fund_scorer/pipeline en Stage 6).
    `coerce_float=True` replica el default de read_sql_query: `numeric` de Postgres llega como
    Decimal, y pandas escribe un Decimal en Excel como TEXTO, no como número.
    """
    cur = conn.execute(query)
    cols = [d[0] for d in cur.description]
    if chunk_size > 0:
        frames = []
        while batch := cur.fetchmany(chunk_size):
            frames.append(pd.DataFrame.from_records(
                [tuple(r) for r in batch], columns=cols, coerce_float=True))
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame.from_records(
            [tuple(r) for r in cur.fetchall()], columns=cols, coerce_float=True)
    return _drop_timezones(df)


def _drop_timezones(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas timestamptz (tz-aware) → hora local naive; to_excel rechaza datetimes con tz."""
    for col in df.columns:
        s = df[col]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            df[col] = s.dt.tz_convert(_EXPORT_TZ).dt.tz_localize(None)
        elif s.dtype == object:
            first = s.dropna().head(1)
            if len(first) and isinstance(first.iloc[0], datetime) and first.iloc[0].tzinfo is not None:
                df[col] = pd.to_datetime(s, utc=True).dt.tz_convert(_EXPORT_TZ).dt.tz_localize(None)
    return df


def _drop_excluded(df: pd.DataFrame, exclude_cols: list) -> pd.DataFrame:
    """Quita exclude_cols sin distinguir mayúsculas: Postgres devuelve `raw_kiid_text` aunque el
    llamante pida `Raw_KIID_Text`; con comparación exacta la exclusión NO se aplicaría y el
    export incluiría ~58M caracteres de texto KIID (~500 MB en vez de ~10 MB — el bug que
    documenta la cabecera v17)."""
    wanted = {c.lower() for c in exclude_cols}
    drop = [c for c in df.columns if str(c).lower() in wanted]
    return df.drop(columns=drop) if drop else df


# ============================================================
# Motor de exportación
# ============================================================

def export_tables(
    tables:      list[TableExportConfig],
    output_path: Path,
    db_path:     Path,
    verbose:     bool = True,
    backend:     Optional[str] = None,
) -> Path:
    """
    Exporta una lista de tablas a un fichero Excel multi-hoja.

    Parámetros:
        tables:       lista de TableExportConfig con las tablas a exportar.
        output_path:  ruta completa del fichero .xlsx a generar.
        db_path:      ruta a fondos.sqlite (solo aplica si el backend resuelto es "sqlite").
        verbose:      si True, imprime progreso por consola.
        backend:      None (resuelve FONDOS_DB_BACKEND, "sqlite" por defecto), "sqlite" o
                      "postgres" (FONDOS_PG_DSN). Ver shared.db.get_connection.

    Devuelve la ruta del fichero generado.

    El fichero se sobreescribe si ya existe.
    El directorio de salida se crea automáticamente si no existe.

    Los errores por tabla se acumulan y se reportan al final;
    un fallo en una tabla no aborta el export de las restantes.

    Cabeceras: las que devuelve cada motor. SQLite conserva la grafía histórica (`ISIN`,
    `Fund_Name`); Postgres devuelve los nombres lower_snake de db/pg/rename_map.yaml (`isin`,
    `fund_name`) — sin mapa inverso, un `SELECT *` exportado desde Postgres cambia las cabeceras
    del Excel respecto al de SQLite.
    """
    output_path = _resolve_writable_path(Path(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # get_connection lanza FileNotFoundError si el backend resuelto es sqlite y la BD no existe.
    conn = get_connection(Path(db_path), backend=backend)

    if verbose:
        print(f"\nExportación -> {output_path}")
        print(f"BD:            {db_path if backend != 'postgres' else '(Postgres: FONDOS_PG_DSN)'}")
        print(f"Tablas:        {len(tables)}\n")

    errors: list[str] = []

    try:
        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            for cfg in tables:
                try:
                    query = cfg.build_query(conn)

                    # chunk_size > 0: lectura por bloques para tablas grandes
                    df = _read_table_df(conn, query, cfg.chunk_size)

                    # Excluir columnas (solo cuando NO se usó include_cols)
                    if not cfg.include_cols and cfg.exclude_cols:
                        df = _drop_excluded(df, cfg.exclude_cols)

                    df.to_excel(
                        writer,
                        sheet_name=cfg.effective_sheet,
                        index=False,
                    )

                    if verbose:
                        mode = (
                            f"include={len(cfg.include_cols)}cols"
                            if cfg.include_cols
                            else f"exclude={len(cfg.exclude_cols)}cols"
                            if cfg.exclude_cols
                            else "all cols"
                        )
                        note = (
                            f"  (limit {cfg.row_limit:,})"
                            if cfg.row_limit else ""
                        )
                        print(
                            f"  [{cfg.effective_sheet}]  "
                            f"{len(df):>8,} filas x {len(df.columns):>3} cols"
                            f"  [{mode}]{note}"
                        )

                except Exception as exc:
                    msg = f"ERROR en {cfg.table}: {exc}"
                    errors.append(msg)
                    if verbose:
                        print(f"  {msg}")
                    # Postgres aborts the WHOLE transaction on a failed statement, so without this
                    # every later table would fail with InFailedSqlTransaction and the "one bad
                    # table doesn't abort the rest" guarantee above would silently not hold. A
                    # no-op on SQLite (read-only queries leave no open transaction to roll back).
                    conn.rollback()

    finally:
        conn.close()

    if verbose:
        size_mb = output_path.stat().st_size / 1_048_576
        print(f"\nFichero generado: {output_path}  ({size_mb:.1f} MB)")
        if errors:
            print(f"\nErrores ({len(errors)}):")
            for e in errors:
                print(f"  {e}")

    return output_path


# ============================================================
# Helpers de nomenclatura
# ============================================================

def dated_filename(prefix: str, ext: str = "xlsx") -> str:
    """
    Genera un nombre de fichero con fecha del día: '<prefix>_YYYYMMDD.<ext>'

    Ejemplo: dated_filename("p1_export") -> "p1_export_20260401.xlsx"
    """
    return f"{prefix}_{date.today().strftime('%Y%m%d')}.{ext}"
