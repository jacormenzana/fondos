-- ============================================================
-- MIGRACIÓN SCHEMA v2 — fondos.sqlite
-- Aplicar sobre BD existente (no borra datos)
-- Fecha: 2026-03-28
-- ============================================================

-- 1. Añadir Accumulation_Policy a fund_master
ALTER TABLE fund_master ADD COLUMN Accumulation_Policy TEXT;
    -- ACCUMULATION | DISTRIBUTION | NULL (no detectado)

-- 2. series_inflation — recrear con PK compuesta (date, geography)
-- SQLite no permite ALTER TABLE para cambiar PK, hay que recrear
CREATE TABLE IF NOT EXISTS series_inflation_new (
    date        DATE    NOT NULL,
    geography   TEXT    NOT NULL DEFAULT 'ES',
    ipc_index   REAL    NOT NULL,
    source      TEXT,
    load_ts     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (date, geography)
);
INSERT OR IGNORE INTO series_inflation_new
    SELECT date, geography, ipc_index, source, load_ts
    FROM series_inflation;
DROP TABLE series_inflation;
ALTER TABLE series_inflation_new RENAME TO series_inflation;

-- 3. KIID_Status CHECK — SQLite no permite añadir CHECK a tabla existente
-- La constraint solo aplica a nuevas inserciones desde la app (ya validado en io.py)
-- No requiere migración de datos — los valores existentes son válidos

-- ============================================================
-- Verificación post-migración
-- ============================================================
-- SELECT COUNT(*) FROM fund_master WHERE Accumulation_Policy IS NOT NULL;
-- SELECT COUNT(*), geography FROM series_inflation GROUP BY geography;
