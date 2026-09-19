# P4_BI_CHARTER — Pipeline de Analytics / BI Sync

**Propósito:** Documento canónico del Pipeline P4: sincronización SQLite → Postgres + visualización
en Superset.

**⚠ EN TRANSICIÓN (desde 2026-09-17):** el 2026-09-17 se decidió migrar la base de datos operacional
completa (P1+P2+P3) a PostgreSQL y retirar SQLite — ver el plan de migración
`role-you-are-keen-ladybug.md` (histórico de sesión) y el memo de proyecto asociado. Esto **supera
en alcance** la arquitectura descrita en este documento: la réplica BI de solo lectura descrita más
abajo (§1-§6) deja de ser el fin último y pasa a ser un subconjunto/precursor de la migración
completa. La §7 "Postgres solo para analytics" ya **no es cierta** como restricción futura — es
la restricción del estado *anterior* a la migración, documentada aquí por completitud histórica.

**Estado real a fecha de esta revisión:** los artefactos de infraestructura P1+P2 de la migración
completa ya están commiteados (`docker/docker-compose.yml`, `docker/postgresql.conf`,
`db/pg/00_roles_schemas.sql` … `40_matviews.sql`, `db/pg/rename_map.yaml`) junto con el loader de
seed y el gate de reconciliación (`scripts/mig/pg_seed.py`, `scripts/mig/pg_reconcile.py`) — pero
**nada de esto se ha desplegado todavía contra el Dell OptiPlex objetivo**. La réplica BI descrita
en §1-§8 sigue siendo el único pipeline P4 en producción hoy. No confundir "artefactos committeados"
con "migración ejecutada".

---

## 0. Arquitectura objetivo (post-migración) — resumen

La base completa (P1+P2+P3, ~25 tablas, ~49.5M filas) migra a PostgreSQL 17 en Docker sobre Ubuntu
24.04, con cuatro esquemas Medallion (`bronze`/`silver`/`gold`/`control`) como frontera de permisos,
`gold.fund_metric_timeseries` particionada por `metric` (la única tabla particionada), y un
reconciliation gate de igualdad exacta antes de cualquier cutover. Detalle completo, incluyendo el
diseño físico (particionado, clustering, índices), la estrategia de backup/PITR, y el plan de
rollback en tres etapas: ver el plan de migración citado arriba.

**Artefactos ya existentes (P1/P2 de la migración):**

| Artefacto | Ruta | Propósito |
|---|---|---|
| Stack Docker | `docker/docker-compose.yml`, `docker/postgresql.conf` | Motor Postgres 17, antes no versionado |
| DDL completo | `db/pg/00_roles_schemas.sql` … `40_matviews.sql` | Esquema objetivo, introspectado desde el `fondos.sqlite` vivo (no desde `db/schema_fondos.sql`, que está desactualizado) |
| Mapa de renombrado | `db/pg/rename_map.yaml` | SQLite mixed-case → PostgreSQL lower_snake, checklist para el sweep de aplicación (§5c del plan) |
| Loader de seed | `scripts/mig/pg_seed.py` | Carga única SQLite→PG, psycopg3 binary COPY, resumible por unidad |
| Gate de reconciliación | `scripts/mig/pg_reconcile.py` | Validación exacta pre-cutover (9 checks) |

**Pendiente (no automatizado por estos artefactos):** aprovisionamiento real del host Ubuntu,
ejecución del seed loader contra un Postgres vivo, el sweep de aplicación de ~86 sitios de conexión
+ 59 upserts (Fase 5 del plan), y las tres etapas de cutover (dual-write → bake de 4 ciclos →
retirada). Ver el plan para el roadmap fase por fase.

---

## 1. Arquitectura general (réplica BI — arquitectura ACTUAL en producción)

```
P3 results (SQLite: fund_metric_timeseries, fund_metric_alerts, fund_master)
    │
    └─► shared/load_fondos_to_postgres.py  (ETL)
            │
            ▼
        Docker Postgres (port 5433)
            │
            └─► Superset (port 8088)  →  Dashboards rolling-signal
```

P4 es unidireccional (P3 → Postgres). No escribe de vuelta a SQLite.

**Nota de alcance real del ETL:** pese a que la tabla §3 solo lista 3 datasets registrados en
Superset, `shared/load_fondos_to_postgres.py` sincroniza **todas** las tablas de SQLite
(`SELECT name FROM sqlite_master`), no solo esas 3 — esto es incidental al diseño del ETL, no una
ruta de migración operacional deliberada (confirmado en el memo de anuncio de la migración,
2026-09-17).

---

## 2. Componentes (réplica BI)

| Componente | Ruta / Endpoint | Propósito |
|-----------|----------------|-----------|
| Launcher | `scripts/launch/P4_syncToPostgres.bat` | Dispara el ETL completo |
| ETL script | `shared/load_fondos_to_postgres.py` | Lee SQLite, escribe Postgres |
| DDL | `db/postgres_analytics_ddl.sql` | Esquema Postgres (one-time) — cubre solo 3 de ~25 tablas |
| Postgres | Docker, `localhost:5433` | Analytics DB (réplica BI — puerto distinto del `5432` de la migración operacional) |
| Superset | Docker, `localhost:8088` | BI / dashboards |

**Cadena de conexión Postgres (réplica BI):**
```
postgresql://superset:superset@localhost:5433/fondos
```

**Nota de convivencia con la migración:** el puerto `5433` (réplica BI) y el `5432` (base
operacional objetivo de la migración) son instancias Docker independientes que pueden convivir
durante la transición. La réplica BI en `5433` permanece activa hasta que la migración cierre su
Fase 4 (vistas y optimización) y Superset se repunte a la base operacional — ver el plan.

---

## 3. Datasets registrados en Superset (réplica BI)

| Dataset | Tabla origen (SQLite) | Descripción |
|---------|----------------------|-------------|
| `fund_metric_timeseries` | `fund_metric_timeseries` | Series temporales de métricas en formato long |
| `fund_metric_alerts` | `fund_metric_alerts` | Alertas de señales rolling |
| `fund_master` | `fund_master` | Dimensión de fondos (filtros, etiquetas) |

---

## 4. Inicialización (one-time, réplica BI)

```batch
:: 1. Crear el esquema en Postgres (ejecutar una sola vez)
psql "postgresql://superset:superset@localhost:5433/fondos" -f db\postgres_analytics_ddl.sql

:: 2. Primera carga completa
scripts\launch\P4_syncToPostgres.bat
```

---

## 5. Actualización incremental (réplica BI)

```batch
:: Ejecutar tras cada ciclo P2 completo
scripts\launch\P4_syncToPostgres.bat
```

**Corrección de deriva documental (2026-09-17):** esta sección afirmaba que "el ETL usa upsert /
INSERT OR REPLACE (Postgres: ON CONFLICT DO UPDATE) sobre las claves primarias de cada tabla" —
**esto nunca fue cierto en el código**. El ETL real usa `pandas.to_sql(if_exists="replace")` para
todas las tablas excepto `fund_metric_timeseries`, que usa un watermark `batch_id` con
`DELETE ... WHERE isin = ANY(...)` seguido de `append` — no hay ningún `ON CONFLICT` en el script.
El resultado es idempotente en la práctica para las tablas de replace completo, pero el mecanismo
descrito aquí no existía. Ver `shared/load_fondos_to_postgres.py` como fuente de verdad, no esta
prosa.

---

## 6. Alternativa local (sin Docker)

`proyecto2/src/reports/rolling_dashboard.py` emite un dashboard HTML autocontenido con Chart.js
incrustado. No requiere Postgres ni Superset. Salida: `c:/data/fondos/reports/rolling_dash_*.html`.
Esta alternativa **no se ve afectada** por la migración operacional — sigue siendo válida
independientemente de qué motor sea el store operacional.

```batch
python -X utf8 -m proyecto2.src.reports.rolling_dashboard
```

---

## 7. Restricciones de arquitectura de la réplica BI (histórico — superadas en alcance por la migración)

- **Unidireccional:** P4 solo lee de SQLite. Nunca escribe de vuelta a SQLite.
- **Sin lógica de negocio:** El ETL es solo transporte. Toda la lógica de cálculo permanece en P2/P3.
- ~~**Postgres solo para analytics:** No sustituye a SQLite como store operacional de P1/P2/P3.~~
  **YA NO ES CIERTO** desde el anuncio de 2026-09-17 — ver §0. Postgres es el objetivo del store
  operacional completo; esta restricción describe el estado anterior, no el objetivo actual.
- **Docker dependency:** Postgres y Superset corren en Docker local. El launcher no arranca Docker;
  los contenedores deben estar activos antes de ejecutar `P4_syncToPostgres.bat`.

---

## 8. Estado operacional

| Componente | Estado |
|-----------|--------|
| ETL réplica BI (`load_fondos_to_postgres.py`) | ACTIVO — pendiente de retirada al cerrar la Fase 4 de la migración |
| Postgres Docker (réplica BI, `:5433`) | ACTIVO |
| Superset Docker (`:8088`) | ACTIVO — repuntado a la base operacional al cerrar la Fase 4 |
| rolling_dashboard.py (alternativa local) | ACTIVO — no afectado por la migración |
| **Artefactos de migración P1/P2** (`docker/`, `db/pg/`, `scripts/mig/pg_seed.py`, `pg_reconcile.py`) | **COMMITEADOS, NO DESPLEGADOS** — ver §0 |
| Base operacional PostgreSQL (`:5432`) | NO INICIADA — aprovisionamiento de host pendiente |

---

*Última revisión: 2026-09-18 — añadida §0 y notas de transición tras el anuncio de migración
completa (2026-09-17). El resto del documento (§1-§8, salvo lo marcado) describe la réplica BI tal
y como existe hoy, no el objetivo.*
