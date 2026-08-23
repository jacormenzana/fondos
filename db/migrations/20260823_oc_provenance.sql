-- ============================================================================
-- 20260823_oc_provenance.sql — one-shot migration (P#7: migración puntual)
--
-- Contexto: la auditoría de distribución de costes (2026-08-23) encontró dos
-- defectos en fund_master.Ongoing_Charge_Recurrent:
--   F1a  81 fondos en escala PORCENTAJE en una columna cuya convención es RATIO
--        (raíz: priips_cost_extractor escribía _ratio_to_pct al rellenar).
--   F1b 801 fondos cuyo valor es en realidad el ACI, no el gasto corriente
--        (raíz: kiid_parser._detect_ongoing_charge cae a la prioridad 1/2, que
--         lee la fila "Incidencia anual", cuando la prioridad 0 "Composición de
--         costes" no casa por texto con columnas entrelazadas).
--
-- Requisito explícito del usuario: NINGÚN dato de componente de coste puede
-- perderse. Por eso esta migración NO corrige nada — solo crea el espacio de
-- preservación y toma una INSTANTÁNEA COMPLETA del estado actual. Cualquier
-- corrección posterior es entonces reversible desde Ongoing_Charge_Legacy.
--
-- Idempotente: reejecutar no vuelve a sobrescribir la instantánea.
-- ============================================================================

-- 1. Columnas de preservación y procedencia -----------------------------------
ALTER TABLE fund_master ADD COLUMN Ongoing_Charge_Legacy REAL;
ALTER TABLE fund_master ADD COLUMN Ongoing_Charge_Source TEXT;

-- 2. Instantánea completa: se preserva TODO valor no nulo actual --------------
UPDATE fund_master
   SET Ongoing_Charge_Legacy = Ongoing_Charge_Recurrent,
       Ongoing_Charge_Source = 'LEGACY_PRE_20260823'
 WHERE Ongoing_Charge_Recurrent IS NOT NULL
   AND Ongoing_Charge_Legacy IS NULL;

-- 3. Verificación -------------------------------------------------------------
--    Debe devolver 0: ningún valor vivo sin su copia preservada.
-- SELECT COUNT(*) FROM fund_master
--  WHERE Ongoing_Charge_Recurrent IS NOT NULL AND Ongoing_Charge_Legacy IS NULL;
