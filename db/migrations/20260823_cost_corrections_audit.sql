-- ============================================================================
-- 20260823_cost_corrections_audit.sql — one-shot migration (P#7)
--
-- Requisito del usuario: ningún dato de componente de coste puede perderse.
-- Ongoing_Charge_Recurrent se resolvió con columnas *_Legacy dedicadas, pero
-- ese patrón no escala: haría falta un par de columnas por cada componente
-- (ACI_RHP, ACI_1Y, Management_Fee_Pct, …).
--
-- Esta tabla generaliza la preservación: un registro por corrección, con el
-- valor anterior, el nuevo, el motivo y el momento. Toda corrección de coste es
-- así reversible sin ampliar el esquema de fund_master cada vez.
--
-- Uso previsto: escribir SIEMPRE aquí ANTES de modificar o anular cualquier
-- componente de coste en fund_master.
-- ============================================================================

CREATE TABLE IF NOT EXISTS fund_cost_corrections (
    ISIN         TEXT    NOT NULL,
    Column_Name  TEXT    NOT NULL,   -- p.ej. 'ACI_RHP'
    Old_Value    REAL,               -- valor preservado (NULL si no había)
    New_Value    REAL,               -- valor tras la corrección (NULL = anulado)
    Reason       TEXT    NOT NULL,   -- código del fix, p.ej. 'F3-PROJECTION-NO-ANCHOR'
    Evidence     TEXT,               -- prueba concreta (fragmento del KID, etc.)
    Corrected_At TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ISIN, Column_Name, Corrected_At)
);

CREATE INDEX IF NOT EXISTS idx_fcc_isin   ON fund_cost_corrections(ISIN);
CREATE INDEX IF NOT EXISTS idx_fcc_reason ON fund_cost_corrections(Reason);
CREATE INDEX IF NOT EXISTS idx_fcc_column ON fund_cost_corrections(Column_Name);
