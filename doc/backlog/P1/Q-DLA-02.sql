-- Q-DLA-02 — KIIDs sospechosos de patología 2-col (heurística textual):**
-- Contar fondos cuyos `Raw_KIID_Text` contienen las firmas léxicas que solo aparecen en texto cruzado:

SELECT COUNT(*) AS n_sospechosos
FROM fund_kiid_metadata
WHERE Raw_KIID_Text REGEXP '\bTipo\s+(tres|cinco|diez)\s+a[ñn]os\b'
   OR Raw_KIID_Text REGEXP '\bsubfondos\s+mide\b'
   OR Raw_KIID_Text REGEXP '\bdurante\s+un\s+per[ií]odo\s+(El|La|Los|Las|Es)\s+'
   OR Raw_KIID_Text REGEXP '\bproducto\s+(Este|Esta)\s+';
