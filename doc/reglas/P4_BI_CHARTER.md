# P4_BI_CHARTER — Pipeline de Analytics / BI Sync

**Propósito:** Documento canónico del Pipeline P4: sincronización SQLite → Postgres + visualización
en Superset. Todo lo que hoy vive solo como prosa en AGENTS.md se consolida aquí.

---

## 1. Arquitectura general

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

---

## 2. Componentes

| Componente | Ruta / Endpoint | Propósito |
|-----------|----------------|-----------|
| Launcher | `scripts/launch/P4_syncToPostgres.bat` | Dispara el ETL completo |
| ETL script | `shared/load_fondos_to_postgres.py` | Lee SQLite, escribe Postgres |
| DDL | `db/postgres_analytics_ddl.sql` | Esquema Postgres (one-time) |
| Postgres | Docker, `localhost:5433` | Analytics DB |
| Superset | Docker, `localhost:8088` | BI / dashboards |

**Cadena de conexión Postgres:**
```
postgresql://superset:superset@localhost:5433/fondos
```

---

## 3. Datasets registrados en Superset

| Dataset | Tabla origen (SQLite) | Descripción |
|---------|----------------------|-------------|
| `fund_metric_timeseries` | `fund_metric_timeseries` | Series temporales de métricas en formato long (~16.6M filas) |
| `fund_metric_alerts` | `fund_metric_alerts` | Alertas de señales rolling |
| `fund_master` | `fund_master` | Dimensión de fondos (filtros, etiquetas) |

---

## 4. Inicialización (one-time)

```batch
:: 1. Crear el esquema en Postgres (ejecutar una sola vez)
psql "postgresql://superset:superset@localhost:5433/fondos" -f db\postgres_analytics_ddl.sql

:: 2. Primera carga completa
scripts\launch\P4_syncToPostgres.bat
```

---

## 5. Actualización incremental

```batch
:: Ejecutar tras cada ciclo P2 completo
scripts\launch\P4_syncToPostgres.bat
```

El ETL usa upsert / INSERT OR REPLACE (Postgres: ON CONFLICT DO UPDATE) sobre las claves primarias
de cada tabla; **la operación es idempotente** — re-ejecutar no duplica datos.

---

## 6. Alternativa local (sin Docker)

`proyecto2/src/reports/rolling_dashboard.py` emite un dashboard HTML autocontenido con Chart.js
incrustado. No requiere Postgres ni Superset. Salida: `c:/data/fondos/reports/rolling_dash_*.html`.

```batch
python -X utf8 -m proyecto2.src.reports.rolling_dashboard
```

---

## 7. Restricciones de arquitectura P4

- **Unidireccional:** P4 solo lee de SQLite. Nunca escribe de vuelta a SQLite.
- **Sin lógica de negocio:** El ETL es solo transporte. Toda la lógica de cálculo permanece en P2/P3.
- **Postgres solo para analytics:** No sustituye a SQLite como store operacional de P1/P2/P3.
- **Docker dependency:** Postgres y Superset corren en Docker local. El launcher no arranca Docker;
  los contenedores deben estar activos antes de ejecutar `P4_syncToPostgres.bat`.

---

## 8. Estado operacional

| Componente | Estado |
|-----------|--------|
| ETL (load_fondos_to_postgres.py) | ACTIVO |
| Postgres Docker | ACTIVO (puerto 5433) |
| Superset Docker | ACTIVO (puerto 8088) |
| rolling_dashboard.py (alternativa local) | ACTIVO |

---

*Última revisión: 2026-08-14*
