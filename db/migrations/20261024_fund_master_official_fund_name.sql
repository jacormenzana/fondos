-- ============================================================================
-- 20261024_fund_master_official_fund_name.sql — one-shot migration (P#7: migración puntual)
--
-- Run as fondos_owner (DDL) against the `fondos` database. Idempotent. NOT run by any session.
--
-- Contexto (FND-0246): la clave de familia de clases (fund_family_builder) se construye con
-- `fund_name`, una cadena de catálogo cortada a ~30 caracteres cuyas abreviaturas varían entre
-- clases del MISMO fondo (451 pares medidos en FND-0244). El nombre oficial del subfondo figura
-- en el KIID y es invariante entre clases; `proyecto1/core/kiid_official_name.py` lo extrae.
--
-- 1. Columna nullable: NULL = "no descubierto" (P#10) -- el texto del KIID no lo expone en su
--    cabecera (UBS, PIMCO, Morgan Stanley, Vontobel: ~8 % de los activos) o aún no hubo pasada P1.
--    Sin DEFAULT, sin CHECK, sin backfill: la rellena la siguiente pasada P1 (COALESCE, P#1)
--    desde `Raw_KIID_Text` cacheado, sin HTTP. Los ISIN retirados quedan NULL y conservan la
--    clave de catálogo.
--
-- ORDEN DE DESPLIEGUE: aplicar ESTA migración ANTES de desplegar el código de la Fase B
-- (shared/schema_checks.py exigirá la columna al arrancar).
-- ============================================================================

ALTER TABLE silver.fund_master ADD COLUMN IF NOT EXISTS official_fund_name text;

-- Verificación (debe devolver 1 fila):
-- SELECT column_name, data_type, is_nullable FROM information_schema.columns
--  WHERE table_schema = 'silver' AND table_name = 'fund_master' AND column_name = 'official_fund_name';
