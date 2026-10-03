-- ============================================================================
-- 20261003_kiid_lifecycle_retire_scope.sql — one-shot migration (P#7: migración puntual)
--
-- Run as fondos_owner (DDL) against the `fondos` database. Idempotent.
--
-- Contexto (FND-0183 follow-up): `kiid_lifecycle` solo decía 'retired'. Hay dos casos
-- distintos de retirada en el catálogo de Deutsche Bank y deben distinguirse:
--   FULL    — el ISIN no tiene NINGÚN documento en el último harvest.
--   PARTIAL — quedan otros documentos pero ya no hay fila KIID.
--
-- 1. Columna + CHECK (nullable: los periodos 'commercializing' no tienen ámbito).
-- 2. Backfill de los periodos 'retired' existentes contra el último harvest.
-- ============================================================================

ALTER TABLE silver.kiid_lifecycle ADD COLUMN IF NOT EXISTS retire_scope text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'kiid_lifecycle_retire_scope_chk') THEN
        ALTER TABLE silver.kiid_lifecycle
            ADD CONSTRAINT kiid_lifecycle_retire_scope_chk
            CHECK (retire_scope IS NULL OR retire_scope IN ('FULL','PARTIAL'));
    END IF;
END $$;

-- Backfill: solo filas retired sin ámbito (reejecutar no sobrescribe nada).
UPDATE silver.kiid_lifecycle l
   SET retire_scope = CASE
         WHEN EXISTS (SELECT 1 FROM bronze.db_document_catalogue c
                       WHERE c.isin = l.isin
                         AND c.harvest_ts = (SELECT MAX(harvest_ts) FROM bronze.db_document_catalogue))
         THEN 'PARTIAL' ELSE 'FULL' END
 WHERE l.status = 'retired'
   AND l.retire_scope IS NULL
   -- Malformed keys ('IE00B45H7020_20260522', 'LU2598446222 - copia') are stray archived PDF
   -- copies, not funds: no scope.
   AND l.isin ~ '^[A-Z]{2}[A-Z0-9]{9}[0-9]$'
   -- A fund that was retired in the past and later came back (it has a 'commercializing' period)
   -- has no knowable historical scope: leave NULL instead of guessing from today's harvest.
   AND NOT EXISTS (SELECT 1 FROM silver.kiid_lifecycle a
                    WHERE a.isin = l.isin AND a.status = 'commercializing');

-- Verificación: solo deben quedar sin ámbito los periodos retirados de fondos que volvieron.
-- SELECT COUNT(*) FROM silver.kiid_lifecycle l WHERE status='retired' AND retire_scope IS NULL
--    AND NOT EXISTS (SELECT 1 FROM silver.kiid_lifecycle a WHERE a.isin=l.isin AND a.status='commercializing');  -- 0
